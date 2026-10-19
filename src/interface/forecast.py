"""Explicit public-data research and opt-in encrypted forecast tracking."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlparse

import streamlit as st

from src.data_vault import DataVaultError
from src.forecaster.models import ResearchPrediction, ResearchRequest, TradingSession

SNAPSHOT_PREFIX = "forecast_snapshot_"
OUTCOME_PREFIX = "forecast_outcome_"


def snapshot_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
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
            st.success("Saved encrypted snapshot." if created else "Already saved unchanged.")
        except (DataVaultError, ValueError) as exc:
            st.error(str(exc))


def _scenario_factory(vault):
    from src.ai_engine.engine import build_chat_client, client_kwargs_from_config
    from src.config import (
        DEFAULT_OLLAMA_HOST, FORECAST_SCENARIO_DEADLINE_SECONDS,
        PERSONAL_AI_LLM_ENGINE, PERSONAL_AI_OMLX_HOST,
    )

    config = vault.retrieve_data("ollama_config") or {}
    options = client_kwargs_from_config(config) if config else {}
    allowed = {"engine", "host", "model", "temperature", "max_tokens", "context_window_tokens"}
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
        not local or endpoint.scheme not in {"http", "https"}
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
    ):
        raise ValueError("Forecast specialists require a loopback inference endpoint")
    kwargs["host"] = host

    def factory(*, remaining_seconds=FORECAST_SCENARIO_DEADLINE_SECONDS):
        return build_chat_client(
            **kwargs, request_timeout_seconds=remaining_seconds,
            safe_local_transport=True,
        )

    return factory


def _research(vault):
    from src.forecaster.research import ResearchPredictionService

    with st.form("forecast_research"):
        symbol = st.text_input("Public US ticker", placeholder="AAPL")
        horizons = st.multiselect("Trading-session horizons", [5, 21], default=[5, 21])
        st.caption(
            "Optional schedule for current-session close eligibility and outcome tracking. "
            "Without it, current-day candles are excluded conservatively and research is unscheduled."
        )
        schedule = st.text_area(
            "Official session sequence (JSON array)",
            placeholder='[{"exchange":"XNAS","session":"2026-10-19",'
            '"closes_at":"2026-10-19T20:00:00Z"}, ...]',
        )
        confirmed = st.checkbox("I verified this is a complete official exchange-session sequence")
        requested = st.form_submit_button("Research ticker")
    if requested:
        try:
            sessions = ()
            if schedule.strip():
                if not confirmed:
                    raise ValueError("Official session sequence requires explicit attestation")
                values = json.loads(schedule)
                if not isinstance(values, list):
                    raise ValueError("Session sequence must be a JSON array")
                sessions = tuple(TradingSession.model_validate(item) for item in values)
            request = ResearchRequest(
                symbol=symbol, horizons=tuple(horizons), sessions=sessions,
            )
            with st.spinner("Researching public sources…"):
                prediction = asyncio.run(ResearchPredictionService().research(request))
            st.session_state["forecast_research_result"] = prediction.to_api()
            st.session_state["forecast_scenarios"] = None
            st.session_state["forecast_prepared"] = None
        except Exception:
            st.error("Research unavailable or invalid ticker/horizons. No forecast was saved.")
    raw = st.session_state.get("forecast_research_result")
    if not raw:
        return
    prediction = ResearchPrediction.model_validate(raw)
    st.subheader(f"{prediction.snapshot.symbol} · {prediction.status}")
    st.warning(prediction.disclaimer)
    st.caption(
        f"As of {prediction.snapshot.as_of.isoformat()} · "
        f"snapshot {prediction.snapshot.snapshot_id} · scorer {prediction.scorer_version}"
    )
    for horizon in prediction.horizons:
        st.write(
            f"**{horizon.horizon_sessions} sessions · {horizon.outlook}** — "
            f"heuristic {horizon.heuristic_score:.2f}, coverage {horizon.coverage:.0%}, "
            f"expected excess return {horizon.expected_excess_return_pct:.2f}%"
        )
        for reason in horizon.reasoning:
            st.caption(reason)
    for limitation in prediction.limitations:
        st.caption(limitation)
    for issue in prediction.snapshot.issues:
        st.warning(f"{issue.source_id}: {issue.code} — {issue.message}")
    st.subheader("Provider provenance")
    st.json([item.to_api() for item in prediction.source_provenance], expanded=False)
    st.caption("Public evidence only; no vault documents, chat, holdings, or account data.")
    if st.button("Run local specialists"):
        from src.forecaster.scenarios import ScenarioSwarmService

        try:
            with st.spinner("Running local public-evidence specialists…"):
                report = asyncio.run(
                    ScenarioSwarmService(client_factory=_scenario_factory(vault)).analyze(
                        prediction.snapshot
                    )
                )
            st.session_state["forecast_scenarios"] = report.to_api()
            st.session_state["forecast_prepared"] = None
        except Exception:
            st.error("Local specialists unavailable. Research remains unchanged; nothing saved.")
    st.caption(
        "Specialist requests disable redirects/environment proxies and use finite connect/read "
        "timeouts bounded by the remaining run budget. These are inactivity timeouts, not hard "
        "cancellation: a continuously active server may outlive the run. "
        "No tools or vault context are available to specialists."
    )
    scenarios = st.session_state.get("forecast_scenarios")
    if scenarios:
        st.subheader("Specialist scenarios")
        st.json(scenarios, expanded=False)
    _prepare_forecast(vault, prediction, scenarios)


def _prepare_forecast(vault, prediction, scenarios):
    from src.forecaster.outcomes import create_forecast

    st.subheader("Prepare an outcome-tracked forecast")
    st.info(
        "Research is unscheduled until you supply a complete official exchange-session "
        "sequence including the origin and every intervening session through the target. "
        "No holiday calendar is fetched or verified. Calendar-day/weekend guesses are not supported."
    )
    sessions = prediction.snapshot.sessions
    if not sessions:
        st.caption(
            "Research is unscheduled. Supply and attest the sequence above, then click "
            "Research ticker again before preparing a tracked forecast."
        )
        return
    st.caption("Uses the exact attested sequence captured with this research, not unsent form edits.")
    st.json([item.to_api() for item in sessions], expanded=False)
    with st.form("forecast_schedule"):
        horizon = st.selectbox(
            "Forecast horizon", [item.horizon_sessions for item in prediction.horizons]
        ) if prediction.horizons else None
        prepare = st.form_submit_button("Prepare forecast")
    if prepare:
        try:
            if horizon is None:
                raise ValueError("A supported researched horizon is required")
            forecast = create_forecast(prediction, horizon, sessions=sessions)
            if forecast.target is None:
                raise ValueError("Official sequence does not include the target session")
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
                "session_sequence": [item.to_api() for item in sessions],
                "calendar_assertion": "user-verified complete official sequence; not independently verified",
            }
            st.session_state["forecast_prepared"] = (payload, snapshot_digest(payload))
        except Exception:
            st.session_state["forecast_prepared"] = None
            st.error("Cannot prepare forecast. Check schedule, origin, horizon, and market coverage.")
    _save_preview(vault, "forecast_prepared", SNAPSHOT_PREFIX, "Save forecast")


def _outcomes(vault):
    from src.forecaster.outcomes import ForecastRecord, SessionPrice, evaluate_outcome

    st.subheader("Saved forecasts and outcomes")
    keys = sorted(key for key in vault.list_keys() if key.startswith(SNAPSHOT_PREFIX))
    if not keys:
        st.caption("No saved forecasts. Research and evaluation never save automatically.")
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
    st.session_state.setdefault("forecast_evaluated_at", datetime.now(timezone.utc).isoformat())
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
                forecast, prices=prices,
                evaluated_at=datetime.fromisoformat(evaluated_at.replace("Z", "+00:00")),
            )
            payload = {
                "schema_version": "forecast-outcome-v1",
                "forecast_id": forecast.forecast_id,
                "outcome": outcome.to_api(),
                "prices": [price.to_api() for price in prices],
            }
            payload["record_id"] = snapshot_digest(payload)
            st.session_state["forecast_evaluation"] = (payload, snapshot_digest(payload))
        except Exception:
            st.session_state["forecast_evaluation"] = None
            st.error("Evaluation unavailable. Check explicit session prices and aware timestamps.")
    prepared = st.session_state.get("forecast_evaluation")
    if prepared and prepared[0]["forecast_id"] == forecast.forecast_id:
        st.write("Outcome/status", prepared[0]["outcome"])
        _save_preview(vault, "forecast_evaluation", OUTCOME_PREFIX, "Save outcome")
    for key in sorted(key for key in vault.list_keys() if key.startswith(OUTCOME_PREFIX)):
        saved = vault.retrieve_data(key)
        if saved and saved.get("forecast_id") == forecast.forecast_id:
            st.write("Saved outcome/status", saved["outcome"])


def render_forecast_page(vault):
    st.header("Forecast")
    st.caption("Requested US ticker research · 5/21 trading sessions · no trading or scheduling.")
    st.warning("Heuristic scores and model scenarios are not calibrated probabilities or financial advice.")
    st.info(
        "Research ticker explicitly requests public market/provider data. Local specialists are "
        "a separate action using existing inference settings. Only Save forecast / Save outcome "
        "writes the displayed snapshot to the encrypted vault."
    )
    try:
        _research(vault)
        _outcomes(vault)
    except (DataVaultError, ValueError, TypeError):
        st.error("Forecast data unavailable. Nothing was automatically saved or modified.")
