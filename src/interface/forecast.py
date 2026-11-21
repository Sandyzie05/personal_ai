"""Explicit public-data research and opt-in encrypted forecast tracking."""

import asyncio
import re
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from src.data_vault import DataVaultError
from src.forecaster.models import ResearchPrediction, ResearchRequest, TradingSession

SNAPSHOT_PREFIX = "forecast_snapshot_"
OUTCOME_PREFIX = "forecast_outcome_"


def snapshot_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def save_rendered_snapshot(vault, prefix: str, payload: dict, approved_digest: str):
    """Consent binds to exact content, not a mutable selection or latest quote."""
    if prefix not in (SNAPSHOT_PREFIX, OUTCOME_PREFIX):
        raise ValueError("Unsupported forecast record namespace")
    if snapshot_digest(payload) != approved_digest:
        raise ValueError("Snapshot changed; prepare and review it again before saving")
    return vault.store_immutable_data(prefix + payload["record_id"], payload)


def _save_preview(vault, state_key: str, prefix: str, label: str):
    prepared = st.session_state.get(state_key)
    if not prepared:
        return
    payload, approved_digest = prepared
    if snapshot_digest(payload) != approved_digest:
        st.error("Snapshot changed. Prepare and review it again; saving is disabled.")
        return
    st.caption("Exact immutable record below. Nothing is saved until you click Save.")
    st.json(payload, expanded=False)
    if st.button(label, key=f"{state_key}_{approved_digest}"):
        try:
            created = save_rendered_snapshot(vault, prefix, payload, approved_digest)
            st.success(
                "Saved encrypted snapshot." if created else "Already saved unchanged."
            )
        except (DataVaultError, ValueError) as exc:
            st.error(str(exc))


def _scenario_factory(vault):
    from src.ai_engine.engine import (
        build_chat_client,
        client_kwargs_from_config,
        worker_client,
    )
    from src.config import (
        DEFAULT_OLLAMA_HOST,
        FORECAST_SCENARIO_DEADLINE_SECONDS,
        PERSONAL_AI_LLM_ENGINE,
        PERSONAL_AI_OMLX_HOST,
    )

    config = vault.retrieve_data("ollama_config") or {}
    options = client_kwargs_from_config(config) if config else {}
    allowed = {
        "engine",
        "host",
        "model",
        "temperature",
        "max_tokens",
        "context_window_tokens",
    }
    kwargs = {key: value for key, value in options.items() if key in allowed}
    engine = kwargs.get("engine", PERSONAL_AI_LLM_ENGINE)
    host = kwargs.get("host") or (
        PERSONAL_AI_OMLX_HOST if engine == "omlx" else DEFAULT_OLLAMA_HOST
    )
    endpoint = urlparse(host)
    try:
        local = ip_address(endpoint.hostname or "").is_loopback
    except ValueError:
        local = endpoint.hostname == "localhost"
    if (
        not local
        or endpoint.scheme not in {"http", "https"}
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Forecast specialists require a loopback inference endpoint")
    kwargs["host"] = host

    def factory(*, remaining_seconds=FORECAST_SCENARIO_DEADLINE_SECONDS):
        return worker_client(
            build_chat_client(
                **kwargs,
                request_timeout_seconds=remaining_seconds,
                safe_local_transport=True,
            )
        )

    return factory


_HEX_ID = re.compile(r"\(?\s*(?:evidence_id:\s*)?\b[0-9a-f]{16,64}\b\s*\)?")
REASON_TEXT = {
    "empty": "empty reply",
    "not_json": "reply was not valid JSON",
    "truncated": "reply cut off at the token limit",
    "schema": "reply missed a required field or length limit",
    "unknown_evidence": "cited evidence that is not in the snapshot",
    "invented_number": "used a number that is not in the evidence",
}
TERMINATION_TEXT = {
    "converged": "the leading thesis was clear after the first round",
    "depth_limit": "the search used its full depth",
    "all_pruned": "every thesis was pruned",
    "no_evidence": "there was no usable evidence",
}
ROLE_LABEL = {
    "macro": "Macro",
    "valuation": "Valuation",
    "event": "Event",
    "downside": "Downside",
}


def _research(vault):
    from src.forecaster.research import ResearchPredictionService

    tabs = _tabs_module()
    st.subheader("1 · Evidence")
    # A widget's state can only be set before it is created, so the draft fill lands on the next run.
    pending = st.session_state.pop("forecast_schedule_pending", None)
    if pending is not None:
        st.session_state["forecast_schedule_text"] = pending
    last = (st.session_state.get("forecast_research_result") or {}).get(
        "snapshot", {}
    ).get("market") or {}
    with st.form("forecast_research"):
        left, right = st.columns([2, 2])
        symbol = left.text_input(
            "Public US ticker", placeholder="AAPL", key="forecast_symbol"
        )
        horizons = right.multiselect(
            "Trading-session horizons", [5, 21], default=[5, 21]
        )
        with st.expander("Advanced: outcome tracking (optional)"):
            st.caption(
                "To save a forecast that can be scored later, the app needs the exchange's trading days from before "
                "today through the target. 1) Pick the listing exchange. 2) Click *Fill draft session list* (built "
                "offline from NYSE holiday rules; it misses one-off closures). 3) Check it against the official "
                "calendar, tick the box, then *Research ticker*. Step 4 then lets you save the forecast."
            )
            exchanges = ["XNAS", "XNYS"]
            exchange = st.selectbox(
                "Listing exchange",
                exchanges,
                index=exchanges.index(last["exchange"])
                if last.get("exchange") in exchanges
                else 0,
                help="Nasdaq = XNAS (e.g. AAPL, ADBE, AVGO), NYSE = XNYS. After one research run it is preselected.",
                key="forecast_schedule_exchange",
            )
            schedule = st.text_area(
                "Session sequence (JSON array)",
                key="forecast_schedule_text",
                height=120,
                placeholder='[{"exchange":"XNAS","session":"2026-10-19",'
                '"closes_at":"2026-10-19T20:00:00Z"}, ...]',
            )
            fill = st.form_submit_button("Fill draft session list")
            confirmed = st.checkbox(
                "I verified this is a complete official exchange-session sequence"
            )
        requested = st.form_submit_button("Research ticker", type="primary")
    if fill:
        from zoneinfo import ZoneInfo

        from src.forecaster.sessions import draft_sessions

        today = datetime.now(ZoneInfo("America/New_York")).date()
        st.session_state["forecast_schedule_pending"] = json.dumps(
            [s.to_api() for s in draft_sessions(exchange, today)], indent=0
        )
        st.rerun()
    if requested:
        try:
            sessions = ()
            if schedule.strip():
                if not confirmed:
                    raise ValueError(
                        "Official session sequence requires explicit attestation"
                    )
                values = json.loads(schedule)
                if not isinstance(values, list):
                    raise ValueError("Session sequence must be a JSON array")
                sessions = tuple(TradingSession.model_validate(item) for item in values)
            request = ResearchRequest(
                symbol=symbol,
                horizons=tuple(horizons),
                sessions=sessions,
            )
            with st.spinner("Researching public sources…"):
                prediction = asyncio.run(ResearchPredictionService().research(request))
            st.session_state["forecast_research_result"] = prediction.to_api()
            for key in (
                "forecast_scenarios",
                "forecast_prepared",
                "forecast_tree",
                "forecast_explanation",
            ):
                st.session_state[key] = None
        except (
            ValueError,
            TypeError,
        ) as exc:  # includes pydantic ValidationError; messages are code-written
            msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
            st.error(f"Research not run: {msg[:200]}. No forecast was saved.")
        except Exception:
            st.error(
                "Research unavailable or invalid ticker/horizons. No forecast was saved."
            )
    raw = st.session_state.get("forecast_research_result")
    if not raw:
        st.caption(
            "Enter a ticker and press Research ticker. Nothing is fetched until you do."
        )
        return
    prediction = ResearchPrediction.model_validate(raw)
    snap = prediction.snapshot
    st.subheader(f"{snap.symbol} · {prediction.status}")
    st.warning(prediction.disclaimer)
    cols = st.columns(max(1, len(prediction.horizons)) + 1)
    last = snap.market.bars[-1] if snap.market and snap.market.bars else None
    cols[0].metric(
        "Last close",
        f"{(last.adjusted_close or last.close):.2f}" if last else "n/a",
        help=f"Session {last.session}" if last else None,
    )
    for col, h in zip(cols[1:], prediction.horizons):
        col.metric(
            f"{h.horizon_sessions}-session outlook",
            h.outlook,
            f"{h.expected_excess_return_pct:+.2f}% vs benchmark",
            delta_color="off",
            help=f"Heuristic score {h.heuristic_score:.2f}, coverage {h.coverage:.0%}. Uncalibrated.",
        )
    if snap.market:
        tabs.price_chart(snap.market)
    sources = {p.source_id for p in prediction.source_provenance}
    issues = {i.source_id: i for i in snap.issues}
    st.caption(
        "Sources: "
        + " · ".join(
            f"{sid} {'⚠ ' + issues[sid].code if sid in issues else '✓'}"
            for sid in sorted(sources | set(issues))
        )
    )
    for issue in snap.issues:
        st.warning(f"{issue.source_id}: {issue.code} — {issue.message}")
    with st.expander("Why these scores"):
        if all(h.drivers for h in prediction.horizons) and prediction.horizons:
            st.caption(
                "Score = 0.50 baseline + each signal's effect (clamped 0.08-0.92). "
                "Positive pushes toward bullish, negative toward bearish."
            )
            first = prediction.horizons[0].drivers
            st.dataframe(
                [
                    {
                        "signal": name,
                        "input": round(value, 3),
                        **{
                            f"{h.horizon_sessions}-session effect": round(
                                h.drivers[name][1], 3
                            )
                            for h in prediction.horizons
                        },
                    }
                    for name, (value, _) in first.items()
                ],
                hide_index=True,
                width="stretch",
            )
            st.dataframe(
                [
                    {
                        "horizon": f"{h.horizon_sessions} sessions",
                        "score": round(h.heuristic_score, 2),
                        "downside": round(h.downside_score, 2),
                        "outlook": h.outlook,
                        "vs benchmark": f"{h.expected_excess_return_pct:+.2f}%",
                    }
                    for h in prediction.horizons
                ],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                f"Data coverage: {prediction.horizons[0].coverage:.0%} of provider families; "
                "missing inputs add nothing."
            )
        else:  # records saved before drivers existed
            for reason in dict.fromkeys(
                r for h in prediction.horizons for r in h.reasoning
            ):
                st.caption(reason)
        st.markdown("**Limits of this research**")
        for limitation in prediction.limitations:
            st.caption(f"• {limitation}")
        st.caption(
            f"As of {snap.as_of:%Y-%m-%d %H:%M} UTC · snapshot {snap.snapshot_id[:12]}… · "
            f"scorer {prediction.scorer_version}"
        )
    with st.expander("Data sources"):
        st.dataframe(
            [
                {
                    "source": p.name,
                    "point-in-time": "yes" if p.point_in_time else "no (latest data)",
                    "caveats": p.caveats,
                }
                for p in prediction.source_provenance
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Public evidence only; no vault documents, chat, holdings, or account data."
        )
    _ai_review(vault, prediction, tabs)
    st.subheader("4 · Track (optional)")
    _prepare_forecast(vault, prediction, st.session_state.get("forecast_scenarios"))


def _ai_review(vault, prediction, tabs):
    from src.agents.slots import CRITIC_SLOTS, agent_for, critic_agents, log_run

    st.subheader("2 · AI review")
    st.caption(
        "Four critic agents review the same frozen public evidence in parallel; a Tree-of-Thought search weighs "
        "competing bullish / base / bearish theses; the explainer agent says why the winner won. No tools, no "
        "vault data. Each agent is tweakable in the Agents tab."
    )
    report_raw = st.session_state.get("forecast_scenarios")
    runs = {r["role"]: r for r in (report_raw or {}).get("specialists", [])}
    chip_cols = st.columns(2)
    for i, (role, slot_id) in enumerate(CRITIC_SLOTS.items()):
        with chip_cols[i % 2]:
            tabs.agent_chip(vault, slot_id, run=runs.get(role))
    tabs.agent_chip(
        vault,
        "forecast.thesis_explainer",
        run=st.session_state.get("forecast_explanation_run"),
    )
    st.caption(
        "Thesis explainer = the writer. The critics and the thesis search decide the verdict; the explainer does not "
        'vote. It only turns the winning thesis and its evidence into ≤ 3 plain sentences ("Why this verdict" under '
        "3 · Verdict). If its reply breaks the rules (invented numbers, unknown evidence), a rule-based summary is shown."
    )
    if st.button("Run AI review", type="primary"):
        from src.forecaster.explain import explain_thesis
        from src.forecaster.scenarios import ScenarioSwarmService
        from src.forecaster.tot import search

        try:
            factory = _scenario_factory(vault)
            with st.spinner("Critic agents reviewing the public evidence…"):
                report = asyncio.run(
                    ScenarioSwarmService(
                        client_factory=factory, agents=critic_agents(vault)
                    ).analyze(prediction.snapshot)
                )
            for result in report.specialists:
                log_run(
                    vault,
                    CRITIC_SLOTS[result.role],
                    result.agent_id,
                    result.status,
                    result.latency_ms,
                    result.invalid_reason,
                )
            tree = search(prediction.snapshot, lambda: report)
            explanation, status, reason, started = (
                None,
                "skipped",
                None,
                datetime.now(timezone.utc),
            )
            if tree.selected_id:
                with st.spinner("Explainer agent writing the verdict…"):
                    explanation, status, reason = explain_thesis(
                        factory(remaining_seconds=60),
                        agent_for(vault, "forecast.thesis_explainer"),
                        prediction.snapshot,
                        tree,
                    )
            ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            log_run(
                vault,
                "forecast.thesis_explainer",
                "builtin-thesis-explainer",
                status,
                ms,
                reason,
            )
            st.session_state["forecast_scenarios"] = report.to_api()
            st.session_state["forecast_tree"] = (
                tree.to_api()
                if hasattr(tree, "to_api")
                else tree.model_dump(mode="json")
            )
            st.session_state["forecast_explanation"] = (
                explanation.model_dump() if explanation else None
            )
            st.session_state["forecast_explanation_run"] = {
                "status": status,
                "invalid_reason": reason,
                "latency_ms": ms,
            }
            st.session_state["forecast_prepared"] = None
            st.rerun()
        except Exception:
            st.error(
                "AI review unavailable (is the local model server running?). Research is unchanged; nothing saved."
            )
    if not report_raw:
        st.caption(
            "Not run yet. Takes up to "
            f"{_deadline_text()} on one laptop; models run locally only."
        )
        return
    st.table(
        [
            {
                "critic": ROLE_LABEL[r["role"]],
                "status": r["status"]
                + (
                    f": {REASON_TEXT.get(r['invalid_reason'], r['invalid_reason'])}"
                    if r.get("invalid_reason")
                    else ""
                ),
                "stance": (r.get("opinion") or {}).get("stance", "—"),
                "confidence": f"{(r.get('opinion') or {}).get('confidence', 0):.2f}"
                if r.get("opinion")
                else "—",
                "seconds": f"{r['latency_ms'] / 1000:.1f}"
                if r.get("latency_ms") is not None
                else "—",
                "evidence cited": len((r.get("opinion") or {}).get("evidence_ids", ())),
            }
            for r in report_raw["specialists"]
        ]
    )
    for r in report_raw["specialists"]:
        if r.get("opinion"):
            with st.expander(
                f"{ROLE_LABEL[r['role']]} critic: {r['opinion']['stance']}"
            ):
                st.text(r["opinion"]["key_reason"])
                st.caption(
                    "What would change this view: "
                    + r["opinion"]["what_changes_my_view"]
                )
                st.caption("Risks: " + "; ".join(r["opinion"]["risks"]))
        elif r.get("reason") and not r.get(
            "invalid_reason"
        ):  # timeout/budget/failed: say why
            st.caption(f"{ROLE_LABEL[r['role']]}: {r['reason']}")
    _verdict(prediction)
    with st.expander("Details: critic diagnostics and thought tree"):
        st.caption(
            f"Critics completed {sum(r['status'] == 'completed' for r in report_raw['specialists'])}/4 · "
            f"model calls {report_raw['model_calls']} · overall {report_raw['verdict']}. "
            "Reply size and stop reason help tell a token-limit cut from a malformed reply."
        )
        st.dataframe(
            [
                {
                    "critic": ROLE_LABEL[r["role"]],
                    "status": r["status"],
                    "model": r.get("model_id") or "—",
                    "reply chars": r.get("output_chars"),
                    "stopped because": r.get("finish_reason") or "—",
                }
                for r in report_raw["specialists"]
            ],
            hide_index=True,
            width="stretch",
        )
        tree = st.session_state.get("forecast_tree") or {}
        if tree.get("nodes"):
            st.dataframe(
                [
                    {
                        "thesis": n["thought"]["scenario"],
                        "driver": n["thought"]["driver"],
                        "round": n["depth"],
                        "score": round(n["score"], 2),
                        "status": n["status"],
                    }
                    for n in tree["nodes"]
                ],
                hide_index=True,
                width="stretch",
            )


def _deadline_text():
    from src.config import FORECAST_SCENARIO_DEADLINE_SECONDS

    return f"{int(FORECAST_SCENARIO_DEADLINE_SECONDS)} s"


def _verdict(prediction):
    from src.forecaster.tot import ThoughtTree

    st.subheader("3 · Verdict")
    raw = st.session_state.get("forecast_tree")
    if not raw:
        return
    tree = ThoughtTree.model_validate(raw)
    pick = tree.node(tree.selected_id) if tree.selected_id else None
    runner = tree.node(tree.runner_up_id) if tree.runner_up_id else None
    with st.container(border=True):
        if pick is None:
            st.markdown(
                f"**Abstained** ({tree.termination}): the evidence does not support any thesis strongly enough."
            )
            return
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Selected scenario",
            pick.thought.scenario,
            f"driver: {pick.thought.driver}",
            delta_color="off",
        )
        c2.metric(
            "Thesis score",
            f"{pick.score:.2f}",
            f"margin {tree.margin:.2f}" if tree.margin is not None else None,
            delta_color="off",
        )
        c3.metric(
            "Runner-up",
            runner.thought.scenario if runner else "none",
            f"{runner.score:.2f}" if runner else None,
            delta_color="off",
        )
        flags = []
        heuristic = next(
            (h.outlook for h in prediction.horizons if h.horizon_sessions == 21), None
        )
        if {heuristic, pick.thought.scenario} == {"bullish", "bearish"}:
            flags.append("disagrees with the heuristic outlook")
        if tree.margin is not None and tree.margin < 0.10:
            flags.append("close call")
        if pick.depth == 2 and "critic" not in pick.components:
            flags.append("critics unavailable")
        if flags:
            st.warning(
                "Uncertain: "
                + ", ".join(flags)
                + ". Treat as research, not a conclusion."
            )
        explanation = st.session_state.get("forecast_explanation")
        if explanation:
            titles = {c.evidence_id: c.title for c in prediction.snapshot.evidence}
            st.markdown("**Why this verdict** (AI explanation)")
            # Evidence ids are listed below by title; strip any the model also pasted into the prose.
            st.text(_HEX_ID.sub("", explanation["summary"]).replace("()", "").strip())
            st.caption(
                "What would change this: "
                + _HEX_ID.sub("", explanation["what_would_change"])
            )
            st.caption(
                "Based on: "
                + "; ".join(titles.get(e, e[:10]) for e in explanation["evidence_ids"])
            )
        else:
            run = st.session_state.get("forecast_explanation_run") or {}
            beat = (
                f", ahead of {runner.thought.scenario} by {tree.margin:.2f}"
                if runner and tree.margin is not None
                else ""
            )
            st.text(
                f"Rule-based summary: the {pick.thought.scenario} thesis wins, driven mainly by "
                f"{pick.thought.driver} evidence{beat}; {TERMINATION_TEXT.get(tree.termination, tree.termination)}."
            )
            why = REASON_TEXT.get(run.get("invalid_reason"), run.get("invalid_reason"))
            st.caption(
                f"AI explanation {run.get('status', 'not run')}"
                + (f": {why}" if why else "")
                + "."
            )
        if tree.critic_calls == 0:
            st.caption(
                "The critics' opinions were not needed for this verdict (the rule-based first round was decisive); "
                "they are shown above for review."
            )


def _prepare_forecast(vault, prediction, scenarios):
    from src.forecaster.outcomes import create_forecast

    sessions = prediction.snapshot.sessions
    if not sessions:
        st.caption(
            "Research is unscheduled, so it cannot be saved for scoring. To track it: step 1 → "
            '"Advanced: outcome tracking" → Fill draft session list → check it → tick the box → Research ticker.'
        )
        return
    st.markdown("**Prepare an outcome-tracked forecast**")
    st.caption(
        "Uses the exact attested sequence captured with this research, not unsent form edits."
    )
    st.json([item.to_api() for item in sessions], expanded=False)
    with st.form("forecast_schedule"):
        horizon = (
            st.selectbox(
                "Forecast horizon",
                [item.horizon_sessions for item in prediction.horizons],
            )
            if prediction.horizons
            else None
        )
        prepare = st.form_submit_button("Prepare forecast")
    if prepare:
        try:
            if horizon is None:
                raise ValueError("A supported researched horizon is required")
            forecast = create_forecast(
                prediction,
                horizon,
                sessions=sessions,
                tree=st.session_state.get("forecast_tree"),
            )  # P8: keep the ToT verdict
            if forecast.target is None:
                raise ValueError(
                    "Official sequence does not include the target session"
                )
            payload = {
                "schema_version": "forecast-v1",
                "record_id": forecast.forecast_id,
                "forecast_id": forecast.forecast_id,
                "snapshot_id": prediction.snapshot.snapshot_id,
                "model_id": prediction.scorer_version,
                "prediction_id": snapshot_digest(prediction.to_api()),
                "forecast": forecast.to_api(),
                "research": prediction.to_api(),
                "scenarios": scenarios,
                "thesis": st.session_state.get("forecast_tree"),
                "session_sequence": [item.to_api() for item in sessions],
                "calendar_assertion": "user-verified complete official sequence; not independently verified",
            }
            st.session_state["forecast_prepared"] = (payload, snapshot_digest(payload))
        except Exception:
            st.session_state["forecast_prepared"] = None
            st.error(
                "Cannot prepare forecast. Check schedule, origin, horizon, and market coverage."
            )
    _save_preview(vault, "forecast_prepared", SNAPSHOT_PREFIX, "Save forecast")


def _outcomes(vault):
    from src.forecaster.outcomes import ForecastRecord, SessionPrice, evaluate_outcome

    keys = sorted(key for key in vault.list_keys() if key.startswith(SNAPSHOT_PREFIX))
    if not keys:
        st.caption(
            "No saved forecasts yet - nothing is saved automatically. To track one: open "
            '"Advanced: outcome tracking" in step 1, paste and confirm the official session sequence, '
            "click Research ticker, then use step 4 (Prepare forecast → Save forecast). Saved forecasts "
            "appear here so you can score them against real closes once the target session has passed."
        )
        return
    selected = st.selectbox("Saved forecast", keys)
    try:
        record = vault.retrieve_data(selected)
        forecast = ForecastRecord.model_validate(record["forecast"])
    except (DataVaultError, ValueError, KeyError, TypeError):
        st.error("Saved forecast could not be read. Existing data was not changed.")
        return
    st.json(record, expanded=False)
    st.caption(
        "Evaluate only against explicit session closes with source provenance. "
        "Missing target-session prices remain missing; latest quotes never substitute."
    )
    st.caption(
        f"Requires {forecast.symbol} ({forecast.origin.exchange}) and "
        f"{forecast.benchmark_symbol} ({forecast.benchmark_exchange}) at both "
        f"origin {forecast.origin.session} and target "
        f"{forecast.target.session if forecast.target else 'unavailable'}, "
        f"using {forecast.price_convention}."
    )
    st.session_state.setdefault(
        "forecast_evaluated_at", datetime.now(timezone.utc).isoformat()
    )
    with st.form("forecast_evaluate"):
        prices_json = st.text_area(
            "Session closing prices (JSON array)",
            placeholder='[{"symbol":"AAPL","exchange":"XNAS","session":"2026-10-19",'
            '"close":100,"source_id":"yahoo-chart","retrieved_at":"2026-10-19T21:00:00Z"}, ...]',
        )
        evaluated_at = st.text_input(
            "Evaluation timestamp (timezone-aware ISO 8601)",
            key="forecast_evaluated_at",
        )
        evaluate = st.form_submit_button("Evaluate outcome")
    if evaluate:
        try:
            values = json.loads(prices_json)
            if not isinstance(values, list):
                raise ValueError("Prices must be a JSON array")
            prices = tuple(SessionPrice.model_validate(item) for item in values)
            outcome = evaluate_outcome(
                forecast,
                prices=prices,
                evaluated_at=datetime.fromisoformat(
                    evaluated_at.replace("Z", "+00:00")
                ),
            )
            payload = {
                "schema_version": "forecast-outcome-v1",
                "forecast_id": forecast.forecast_id,
                "outcome": outcome.to_api(),
                "prices": [price.to_api() for price in prices],
            }
            payload["record_id"] = snapshot_digest(payload)
            st.session_state["forecast_evaluation"] = (
                payload,
                snapshot_digest(payload),
            )
        except Exception:
            st.session_state["forecast_evaluation"] = None
            st.error(
                "Evaluation unavailable. Check explicit session prices and aware timestamps."
            )
    prepared = st.session_state.get("forecast_evaluation")
    if prepared and prepared[0]["forecast_id"] == forecast.forecast_id:
        st.write("Outcome/status", prepared[0]["outcome"])
        _save_preview(vault, "forecast_evaluation", OUTCOME_PREFIX, "Save outcome")
    for key in sorted(
        key for key in vault.list_keys() if key.startswith(OUTCOME_PREFIX)
    ):
        saved = vault.retrieve_data(key)
        if saved and saved.get("forecast_id") == forecast.forecast_id:
            st.write("Saved outcome/status", saved["outcome"])


def _tabs_module():
    """Load sibling forecast_tabs by path (src.interface/__init__ pulls in the whole app)."""
    name = "personal_ai_forecast_tabs"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("forecast_tabs.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def render_forecast_page(vault):
    st.header("Forecast")
    st.caption(
        "Public-evidence stock research with local AI critics · 5/21 trading sessions · never trades."
    )
    st.warning(
        "Heuristic scores and model scenarios are not calibrated probabilities or financial advice."
    )
    tabs = _tabs_module()
    section = (
        st.segmented_control(
            "Section", tabs.SECTIONS, default="Research", key="forecast_section"
        )
        or "Research"
    )
    try:
        if section == "Research":
            st.caption(tabs.SECTION_HELP["Research"])
            _research(vault)
            with st.expander("Saved forecasts and outcomes"):
                _outcomes(vault)
        else:
            tabs.render_section(
                section,
                vault,
                SNAPSHOT_PREFIX,
                OUTCOME_PREFIX,
                client_factory=lambda: _scenario_factory(vault),
            )
    except (DataVaultError, ValueError, TypeError):
        st.error(
            "Forecast data unavailable. Nothing was automatically saved or modified."
        )
