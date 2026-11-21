"""Forecast page sections ported from stock_predictor tabs.

Network happens only inside explicit button handlers. All external text (news
titles/publishers/URLs) is untrusted and rendered with st.text only. Loaded by
path from forecast.py, so import only src.forecaster / src.data_vault / src.agents here
(never src.interface: its __init__ pulls in the whole app).
"""

import asyncio
import time
from datetime import datetime, timezone

import streamlit as st

from src.data_vault import DataVaultError
from src.forecaster.health import HealthTracker
from src.forecaster.models import ResearchRequest, canonical_symbol
from src.forecaster.news import NewsService
from src.forecaster.recommend import rank_leans, research_lean, select_diversified
from src.forecaster.report import report as build_report, to_csv
from src.forecaster.screens import ScreenService
from src.forecaster.technical import technical_snapshot

SECTIONS = ("Research", "Watchlist", "Events", "Screens", "Drilldown", "Track record")
SECTION_HELP = {
    "Research": "One ticker end to end: public evidence, AI critic review, verdict, optional tracking.",
    "Watchlist": "Rank several tickers by a technical research lean; optional AI explanation.",
    "Events": "Today's market news (untrusted text, shown inert) and heuristic per-symbol leanings.",
    "Screens": "Reference screens over a fixed large-cap list using price history only.",
    "Drilldown": "Every technical indicator for one ticker, with a chart, plus provider health.",
    "Track record": "How saved forecasts did after their horizon, against naive baselines.",
}
MAX_LEAN_SYMBOLS = 15
RISKS = ("low", "moderate", "high", "very_high")
SCREEN_LABELS = {
    "period_high": "Near period high",
    "period_low": "Near period low",
    "undervalued": "Undervalued (heuristic)",
    "overvalued": "Overvalued (heuristic)",
    "strong_buy": "Strong upward momentum",
    "strong_sell": "Strong downward momentum",
}


# --- Shared UI pieces ------------------------------------------------------


def _go_tweak(agent_id):
    st.session_state["nav_page"] = "agents"
    st.session_state["agents_editing_id"] = agent_id
    st.session_state["agents_group"] = "Forecast workflow"


def agent_chip(vault, slot_id, *, run=None):
    """Which agent does this AI step, on which model, and how its last run went; one click to tweak it."""
    from src.agents.slots import SLOTS, agent_for

    agent = agent_for(vault, slot_id)
    with st.container(border=True):
        left, right = st.columns([5, 1])
        left.markdown(
            f"{agent.get('icon', '')} **{agent['name']}**"
            + ("" if agent["name"].lower() == SLOTS[slot_id].label.lower() else f" · {SLOTS[slot_id].label}")
        )
        bits = [
            f"model: {agent.get('model') or 'app default'}",
            f"temperature {agent.get('temperature', 0):.2f}",
        ]
        if agent.get("tweaked"):
            bits.append("tweaked")
        if run:
            bits.append(
                f"last run: {run.get('status')}"
                + (f" ({run['invalid_reason']})" if run.get("invalid_reason") else "")
            )
            if run.get("latency_ms") is not None:
                bits.append(f"{run['latency_ms'] / 1000:.1f} s")
        left.caption(" · ".join(bits))
        right.button(
            "Tweak",
            key=f"tweak_{slot_id}",
            on_click=_go_tweak,
            args=(agent["id"],),
            help="Open this agent in the Agents tab",
        )
    return agent


def _sma(values, n):
    return [
        None if i + 1 < n else sum(values[i + 1 - n : i + 1]) / n
        for i in range(len(values))
    ]


def price_chart(market, title=None, height=320):
    """Close with 20/50-session averages; no fetch, just the bars already in hand."""
    import plotly.graph_objects as go

    bars = list(market.bars)
    if len(bars) < 2:
        st.caption("Not enough price history to chart.")
        return
    x = [b.session for b in bars]
    closes = [b.adjusted_close or b.close for b in bars]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=x, y=closes, name="Close", line=dict(color="#2a78d6", width=2))
    )
    for n, color in ((20, "#f39c12"), (50, "#8e44ad")):
        if len(closes) >= n:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=_sma(closes, n),
                    name=f"SMA {n}",
                    line=dict(color=color, width=1),
                )
            )
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30 if title else 10, b=10),
        title=title,
        legend=dict(orientation="h", y=1.08),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


def sparkline(market):
    closes = [b.adjusted_close or b.close for b in market.bars][-30:]
    return closes


def _providers():
    from src.forecaster.providers import PublicDataProviders

    return PublicDataProviders()


def _health() -> HealthTracker:
    return st.session_state.setdefault("forecast_health", HealthTracker())


def _call(source_id, factory):
    """Run one provider coroutine, recording health; returns the ProviderResult."""
    start = time.monotonic()
    result = asyncio.run(factory())
    _health().record(
        source_id,
        result,
        at=datetime.now(timezone.utc),
        latency_ms=(time.monotonic() - start) * 1000,
    )
    return result


def _status(result, label):
    """Explicit status line + provider issues so a partial/unavailable provider is never silent."""
    if result.status == "completed":
        st.caption(f"{label}: completed")
        return
    st.warning(f"{label}: {result.status}")
    for issue in result.issues:
        st.caption(f"{issue.source_id} / {issue.code}: {issue.message}")


def load_pairs(vault, snapshot_prefix, outcome_prefix):
    """(ForecastRecord, latest OutcomeEvaluation|None) pairs plus count of unreadable records."""
    from src.forecaster.outcomes import ForecastRecord, OutcomeEvaluation

    bad, outcomes = 0, {}
    for key in sorted(k for k in vault.list_keys() if k.startswith(outcome_prefix)):
        try:
            outcome = OutcomeEvaluation.model_validate(
                vault.retrieve_data(key)["outcome"]
            )
        except (DataVaultError, ValueError, KeyError, TypeError):
            bad += 1
            continue
        seen = outcomes.get(outcome.forecast_id)
        if seen is None or outcome.evaluated_at > seen.evaluated_at:
            outcomes[outcome.forecast_id] = outcome
    pairs = []
    for key in sorted(k for k in vault.list_keys() if k.startswith(snapshot_prefix)):
        try:
            forecast = ForecastRecord.model_validate(
                vault.retrieve_data(key)["forecast"]
            )
        except (DataVaultError, ValueError, KeyError, TypeError):
            bad += 1
            continue
        pairs.append((forecast, outcomes.get(forecast.forecast_id)))
    return pairs, bad


# --- Watchlist (was Recommendations) ---------------------------------------


async def _market_all(providers, symbols):
    return await asyncio.gather(
        *(providers.market(ResearchRequest(symbol=s)) for s in symbols),
        return_exceptions=True,
    )


def _open_research(symbol):
    st.session_state["forecast_section"] = "Research"
    st.session_state["forecast_symbol"] = symbol


def render_recommendations(vault=None, client_factory=None):
    st.subheader("Watchlist: research leans")
    st.warning(
        "Research lean only: uncalibrated close-only technicals, no sentiment/fundamentals, "
        "no price targets, not a buy/sell instruction or financial advice."
    )
    raw = st.text_input(
        "Public US tickers (comma separated)",
        value="AAPL, MSFT, NVDA",
        key="forecast_lean_symbols",
    )
    if st.button("Compute research leans", type="primary"):
        symbols, rejected = [], []
        for part in dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()):
            try:
                symbols.append(canonical_symbol(part))
            except ValueError:
                rejected.append(part[:8])
        extra = symbols[MAX_LEAN_SYMBOLS:]
        symbols = symbols[:MAX_LEAN_SYMBOLS]
        leans, failed, closes = [], [], {}
        if symbols:
            with st.spinner("Fetching public price history…"):
                start = time.monotonic()
                results = asyncio.run(_market_all(_providers(), symbols))
                ms = (time.monotonic() - start) * 1000
            for symbol, result in zip(symbols, results):
                if isinstance(result, BaseException) or result.value is None:
                    failed.append(
                        (
                            symbol,
                            "unavailable",
                            []
                            if isinstance(result, BaseException)
                            else [f"{i.code}: {i.message}" for i in result.issues],
                        )
                    )
                    continue
                _health().record(
                    "yahoo-chart", result, at=datetime.now(timezone.utc), latency_ms=ms
                )
                leans.append(research_lean(technical_snapshot(result.value)))
                closes[symbol] = sparkline(result.value)
        st.session_state["forecast_leans"] = {
            "leans": leans,
            "failed": failed,
            "rejected": rejected,
            "dropped": extra,
            "closes": closes,
        }
        st.session_state.pop("forecast_lean_notes", None)
    state = st.session_state.get("forecast_leans")
    if not state:
        st.caption(
            "Nothing fetched yet. Leans are computed only when you press the button."
        )
        return
    left, right = st.columns(2)
    max_risk = left.selectbox(
        "Maximum risk level for watchlist",
        RISKS,
        index=len(RISKS) - 1,
        help="Default shows every risk level; lower it to hide volatile tickers.",
    )
    min_conf = right.slider(
        "Minimum confidence",
        0.0,
        0.95,
        0.0,
        0.05,
        help="Technical-only leans carry low confidence by design (no sentiment evidence).",
    )
    ranked = rank_leans(state["leans"])
    picked = select_diversified(ranked, max_risk=max_risk, min_confidence=min_conf)
    notes = st.session_state.get("forecast_lean_notes") or {}
    if picked:
        st.write(f"Diversified watchlist ({len(picked)} of {len(ranked)} ranked)")
        st.dataframe(
            [
                {
                    "rank": p.rank,
                    "symbol": p.lean.symbol,
                    "research lean": p.lean.lean,
                    "confidence": round(p.lean.confidence, 2),
                    "risk": p.lean.risk_level,
                    "overall score": round(p.lean.overall_score, 1),
                    "last 30 closes": state.get("closes", {}).get(p.lean.symbol, []),
                    "why": notes.get(p.lean.symbol) or "; ".join(p.lean.reasoning[:2]),
                }
                for p in picked
            ],
            column_config={
                "last 30 closes": st.column_config.LineChartColumn("last 30 closes")
            },
            hide_index=True,
            width="stretch",
        )
        c1, c2 = st.columns([2, 3])
        target = c1.selectbox(
            "Open in Research",
            [p.lean.symbol for p in picked],
            key="forecast_lean_open",
        )
        c1.button("Research this ticker", on_click=_open_research, args=(target,))
        with c2:
            agent = agent_chip(
                vault,
                "forecast.watchlist_explainer",
                run=st.session_state.get("forecast_lean_notes_run"),
            )
            if client_factory and st.button(
                "Explain with AI", key="forecast_lean_explain"
            ):
                from src.agents.slots import log_run
                from src.forecaster.explain import explain_leans

                start = time.monotonic()
                try:
                    out, status, reason = explain_leans(
                        client_factory()(remaining_seconds=60),
                        agent,
                        [p.lean for p in picked],
                    )
                except Exception:
                    out, status, reason = None, "failed", None
                run = {
                    "status": status,
                    "invalid_reason": reason,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                }
                log_run(
                    vault,
                    "forecast.watchlist_explainer",
                    agent["id"],
                    status,
                    run["latency_ms"],
                    reason,
                )
                st.session_state["forecast_lean_notes_run"] = run
                st.session_state["forecast_lean_notes"] = (
                    {i.symbol: i.note for i in out.items} if out else {}
                )
                st.rerun()
        run = st.session_state.get("forecast_lean_notes_run")
        if run:  # persistent result, not a toast that vanishes on the next rerun
            if notes:
                st.markdown("**AI notes** (also in the *why* column)")
                for symbol, note in notes.items():
                    st.text(f"{symbol}: {note}")
            else:
                why = f": {run['invalid_reason']}" if run.get("invalid_reason") else ""
                st.warning(f"AI explanation {run['status']}{why}; the why column shows rule-based reasons.")
    else:
        st.info(
            "No symbol passed the confidence/risk filters (or none had enough evidence)."
        )
    kept = {p.lean.symbol for p in picked}
    hidden = [
        f"{r.lean.symbol} (risk {r.lean.risk_level})"
        if RISKS.index(r.lean.risk_level) > RISKS.index(max_risk)
        else f"{r.lean.symbol} (confidence {r.lean.confidence:.2f})"
        if r.lean.confidence < min_conf
        else f"{r.lean.symbol} (watchlist size cap)"
        for r in ranked
        if r.lean.symbol not in kept
    ]
    if hidden:
        st.caption("Hidden by the filters above: " + ", ".join(hidden))
    for lean in state["leans"]:
        if lean.status != "completed":
            st.warning(
                f"{lean.symbol}: insufficient evidence. {' '.join(lean.reasoning)}"
            )
    for symbol, status, failed_notes in state["failed"]:
        st.warning(f"{symbol}: market data {status}. {' | '.join(failed_notes)}")
    if state["rejected"]:
        st.warning("Rejected as not US tickers: " + ", ".join(state["rejected"]))
    if state["dropped"]:
        st.warning(
            f"Only the first {MAX_LEAN_SYMBOLS} tickers were processed; skipped: "
            + ", ".join(state["dropped"])
        )


# --- Track record (was Validation) + System health --------------------------


def render_validation(vault, snapshot_prefix, outcome_prefix):
    st.subheader("Track record")
    st.caption(
        "How saved forecasts did once their horizon (5 or 21 trading sessions) has passed: directional accuracy, "
        "coverage (share of forecasts that made a call), abstentions and return versus the benchmark, next to naive "
        "baselines (always-up, persistence). It stays empty until you save forecasts and their horizons elapse; "
        'below 10 scored forecasts it reports "insufficient sample" instead of a percentage.'
    )
    pairs, bad = load_pairs(vault, snapshot_prefix, outcome_prefix)
    if bad:
        st.warning(
            f"{bad} saved record(s) could not be read and were excluded; nothing was modified."
        )
    if not pairs:
        st.info(
            "No saved forecasts yet, so there is nothing to validate. Save one from Research "
            "(Advanced: outcome tracking). No network is used here."
        )
        return
    now = datetime.now(timezone.utc)
    result = build_report(pairs, as_of=now)
    summary = result["summary"]
    cols = st.columns(4)
    cols[0].metric("Forecasts", summary["total_forecasts"])
    cols[1].metric("Evaluated", summary["evaluated"])
    cols[2].metric("Pending", summary["pending"])
    cols[3].metric("Unresolved", summary["unresolved"])
    st.write(f"Directional accuracy: {summary['directional_accuracy_label']}")
    st.write(f"Coverage: {summary['coverage_label']}")
    for line in result["narrative"]:
        st.caption(line)
    st.subheader("Daily breakdown")
    if result["daily"]:
        st.dataframe(result["daily"], hide_index=True)
    else:
        st.caption("No scored days yet (needs evaluated outcomes after target close).")
    with st.expander("Trend and baselines (details)"):
        st.json(
            {"trend": result["trend"], "baselines": summary["baselines"]},
            expanded=False,
        )
    st.download_button(
        "Download validation CSV",
        data=to_csv(pairs, as_of=now),
        file_name="forecast_validation.csv",
        mime="text/csv",
    )


def render_system(vault, snapshot_prefix, outcome_prefix):
    st.subheader("System health")
    from src import config

    keys = {
        "FRED": config.FORECAST_FRED_API_KEY,
        "Alpha Vantage": config.FORECAST_ALPHA_VANTAGE_API_KEY,
        "SEC contact": config.FORECAST_SEC_USER_AGENT,
    }
    st.caption(
        "Optional sources configured: "
        + " · ".join(f"{k} {'yes' if v else 'no'}" for k, v in keys.items())
    )
    pairs, bad = load_pairs(vault, snapshot_prefix, outcome_prefix)
    snap = _health().snapshot(pairs, as_of=datetime.now(timezone.utc))
    st.write(f"Overall provider status: {snap['status']}")
    if snap["providers"]:
        st.table(
            [
                {
                    k: p[k]
                    for k in (
                        "source_id",
                        "state",
                        "calls",
                        "failures",
                        "partials",
                        "last_status",
                        "last_latency_ms",
                    )
                }
                for p in snap["providers"]
            ]
        )
    else:
        st.info(
            "No provider calls yet this session (status unknown). Health fills in as you use other sections."
        )
    if bad:
        st.warning(f"{bad} saved forecast/outcome record(s) unreadable.")
    acc = snap["accuracy"]
    st.write(
        f"Saved forecasts: {acc['total_forecasts']} · evaluated {acc['evaluated']} · "
        f"{acc['directional_accuracy_label']}"
    )


# --- Events ----------------------------------------------------------------


def _stories_table(stories, ai=None):
    """Headlines are untrusted: dataframe cells render as inert text (never markdown/HTML)."""
    ai = ai or {}
    st.dataframe(
        [{"date": f"{s.published_at:%Y-%m-%d}", "headline": s.title, "publisher": s.publisher,
          "tickers": ", ".join(s.related_tickers[:4]), "keyword sentiment": s.sentiment,
          **({"AI sentiment": ai.get(s.story_id, "—")} if ai else {}),
          "impact": round(s.impact_score, 2), "link": s.url} for s in stories],
        column_config={"link": st.column_config.LinkColumn("link", display_text="open"),
                       "headline": st.column_config.TextColumn("headline", width="large")},
        hide_index=True, width="stretch",
    )


def _news_analyst(vault, client_factory, stories, key):
    """F7: tag the shown headlines through quarantine (closed enums only) and compare with the keyword lean."""
    from src.agents.slots import agent_for, log_run
    from src.forecaster.news_analyst import NewsAnalysis, analyze

    state_key = f"forecast_news_ai_{key}"
    agent = agent_chip(vault, "forecast.news_analyst", run=st.session_state.get(state_key + "_run"))
    if client_factory and st.button("Analyze with AI", key=f"forecast_news_ai_btn_{key}"):
        start = time.monotonic()
        try:
            out = analyze(stories, client_factory(), agent_for(vault, "forecast.news_analyst"))
            status = "completed" if out.analyzed else "invalid"
            st.session_state[state_key] = out.to_api()
        except Exception:
            out, status = None, "failed"
            st.session_state[state_key] = None
        ms = int((time.monotonic() - start) * 1000)
        st.session_state[state_key + "_run"] = {"status": status, "latency_ms": ms}
        log_run(vault, "forecast.news_analyst", agent["id"], status, ms, None)
        st.rerun()
    raw = st.session_state.get(state_key)
    if not raw:
        return {}
    out = NewsAnalysis.model_validate(raw)
    by_id = {s.story_id: s for s in stories}
    differ = sum(1 for sid, sent in out.story_sentiment.items() if sid in by_id and by_id[sid].sentiment != sent)
    st.caption(f"AI tagged {out.analyzed} headlines · rejected {out.rejected} · looked like injection {out.flagged}"
               + (f" · not reached (time budget) {out.skipped}" if out.skipped else "")
               + f" · AI and keyword sentiment disagree on {differ}.")
    if out.counts:
        st.dataframe([{"ticker": t, **c.model_dump()} for t, c in sorted(out.counts.items())],
                     hide_index=True, width="stretch")
    return out.story_sentiment


def render_events(vault=None, client_factory=None):
    st.subheader("Market events and news")
    st.warning("Headlines are untrusted external text, shown as plain text. Impact scores are heuristic.")
    st.caption("Keyword sentiment is a crude word match. *Analyze with AI* tags each headline through quarantine: "
               "the News Analyst returns only closed labels (topic, sentiment, materiality), never prose.")
    if st.button("Fetch today's stories"):
        try:
            with st.spinner("Fetching public news…"):
                st.session_state["forecast_news_report"] = _call(
                    "news-service", lambda: NewsService(_providers()).build_today_report())
            st.session_state.pop("forecast_news_ai_today", None)
        except Exception:
            st.session_state["forecast_news_report"] = None
            st.error("News unavailable. Nothing was saved.")
    result = st.session_state.get("forecast_news_report")
    if result is None:
        st.caption("Nothing fetched yet. News is requested only when you press the button.")
    else:
        _status(result, "Today's stories")
        report = result.value
        if report:
            if report.topics_failed:
                st.warning("Topics that failed: " + ", ".join(report.topics_failed))
            cols = st.columns(3)
            cols[0].metric("Stories", len(report.stories))
            cols[1].metric("Upward-leaning symbols", len(report.bullish))
            cols[2].metric("Downward-leaning symbols", len(report.bearish))
            if report.stories:
                ai = _news_analyst(vault, client_factory, report.stories[:10], "today")
                _stories_table(report.stories[:20], ai)
            else:
                st.caption("No stories returned.")
            lean_rows = [{"symbol": x.symbol, "lean": side, "score": round(x.score, 2), "stories": len(x.story_ids)}
                         for side, leans in (("up", report.bullish), ("down", report.bearish)) for x in leans]
            st.write("Keyword leanings by symbol")
            st.dataframe(lean_rows, hide_index=True, width="stretch") if lean_rows else st.caption("None identified.")
            if report.disagreements:
                st.write("Conflicting signals (stories on both sides; not averaged)")
                st.dataframe([{"symbol": d.symbol, "up": round(d.up_score, 2), "down": round(d.down_score, 2),
                               "stronger": d.stronger_side} for d in report.disagreements],
                             hide_index=True, width="stretch")
            st.caption(report.note)
    with st.form("forecast_news_search"):
        query = st.text_input("Search topic or ticker (one word works best, e.g. ADBE, inflation)",
                              key="forecast_news_query")
        go = st.form_submit_button("Search news")
    if go:
        try:
            with st.spinner("Searching public news…"):
                st.session_state["forecast_news_found"] = _call(
                    "news-service", lambda: NewsService(_providers()).search_news(query))
            st.session_state["forecast_news_found_query"] = query
            st.session_state.pop("forecast_news_ai_search", None)
        except Exception:
            st.session_state["forecast_news_found"] = None
            st.error("News search unavailable or invalid query.")
    found = st.session_state.get("forecast_news_found")
    if found is not None:
        from src.forecaster.news import is_relevant

        _status(found, "News search")
        if found.value:
            q = st.session_state.get("forecast_news_found_query", "")
            relevant = [s for s in found.value if is_relevant(s, q)]
            loose = [s for s in found.value if not is_relevant(s, q)]
            if relevant:
                ai = _news_analyst(vault, client_factory, relevant, "search")
                _stories_table(relevant, ai)
            else:
                st.caption("No headline mentions the search term.")
            if loose and st.toggle(f"Show {len(loose)} loosely related stories (term not in headline)",
                                   key="forecast_news_loose"):
                _stories_table(loose)
        elif found.value is not None:
            st.caption("No stories matched.")


# --- Drilldown ---------------------------------------------------------------

INDICATORS = {
    "last_price": "Latest completed close",
    "sma_20": "20-session simple average",
    "sma_50": "50-session average",
    "sma_200": "200-session average (needs 200 bars)",
    "ema_12": "12-session exponential average",
    "ema_26": "26-session exponential average",
    "rsi_14": "Momentum 0-100; >70 stretched up, <30 stretched down",
    "macd": "EMA12 - EMA26",
    "macd_signal": "9-session average of MACD",
    "macd_histogram": "MACD - signal",
    "bollinger_upper": "SMA20 + 2 std dev",
    "bollinger_lower": "SMA20 - 2 std dev",
    "support": "Recent low area",
    "resistance": "Recent high area",
    "annualized_volatility_pct": "Annualised close-to-close volatility",
    "atr": "Average true range (14)",
    "avg_volume_20": "20-session average volume",
    "volume_ratio": "Latest volume / 20-session average",
}


def render_drilldown():
    st.subheader("Technical drilldown")
    symbol = st.text_input(
        "Public US ticker", key="forecast_drill_symbol", placeholder="AAPL"
    )
    if st.button("Load technicals", type="primary"):
        try:
            req = ResearchRequest(symbol=symbol)
            with st.spinner("Fetching public price history…"):
                result = _call("yahoo-chart", lambda: _providers().market(req))
            st.session_state["forecast_drill"] = result
        except Exception:
            st.session_state["forecast_drill"] = None
            st.error("Invalid ticker or provider error. Nothing was saved.")
    result = st.session_state.get("forecast_drill")
    if result is None:
        st.caption("Nothing fetched yet.")
        return
    _status(result, "Market data")
    if result.value is None:
        return
    tech = technical_snapshot(result.value)
    lean = research_lean(tech)
    st.write(
        f"{tech.symbol} · {tech.status} · {tech.bar_count} bars · trend {tech.trend}"
    )
    price_chart(result.value)
    cols = st.columns(4)
    for col, (label, field) in zip(
        cols,
        (
            ("Last close", "last_price"),
            ("RSI 14", "rsi_14"),
            ("Volatility %", "annualized_volatility_pct"),
            ("ATR", "atr"),
        ),
    ):
        value = getattr(tech, field)
        col.metric(label, "n/a" if value is None else f"{value:.2f}")
    st.table(
        [
            {
                "indicator": f,
                "value": "unavailable"
                if getattr(tech, f) is None
                else f"{getattr(tech, f):.2f}",
                "meaning": INDICATORS[f],
            }
            for f in INDICATORS
        ]
    )
    for item in tech.unavailable:
        st.caption(f"Unavailable: {item}")
    if lean.status == "completed":
        st.write(
            f"Research lean: {lean.lean} · confidence {lean.confidence:.2f} · risk {lean.risk_level}"
        )
        for reason in lean.reasoning:
            st.caption(reason)
    else:
        st.warning("Research lean: insufficient evidence. " + " ".join(lean.reasoning))
    st.caption(" ".join(lean.limitations))
    st.button(
        "Research this ticker",
        key="forecast_drill_open",
        on_click=_open_research,
        args=(tech.symbol,),
    )


# --- Screens ---------------------------------------------------------------


def render_screens():
    st.subheader("Reference stock screens")
    st.warning(
        "Reference screens over a fixed large-cap list using price history only; not recommendations."
    )
    kind = st.selectbox(
        "Screen",
        list(SCREEN_LABELS),
        format_func=SCREEN_LABELS.get,
        key="forecast_screen_kind",
    )
    if st.button("Run screen", type="primary"):
        try:
            with st.spinner("Screening public price history…"):
                st.session_state["forecast_screen"] = _call(
                    "screen-service", lambda: ScreenService(_providers()).screen(kind)
                )
        except Exception:
            st.session_state["forecast_screen"] = None
            st.error("Screen unavailable. Nothing was saved.")
    result = st.session_state.get("forecast_screen")
    if result is None:
        st.caption(
            "Nothing fetched yet. Prices are requested only when you press the button."
        )
        return
    _status(result, "Screen")
    screen = result.value
    if screen is None:
        return
    st.caption(f"{SCREEN_LABELS.get(screen.kind, screen.kind)} · {screen.window}")
    st.caption(
        f"Analyzed {screen.symbols_analyzed} of {screen.symbols_requested} symbols."
    )
    if screen.symbols_failed:
        st.warning("No data for: " + ", ".join(screen.symbols_failed))
    if screen.symbols_rejected:
        st.warning("Rejected symbols: " + ", ".join(screen.symbols_rejected))
    if screen.items:
        st.dataframe(
            [
                {
                    "rank": i.rank,
                    "symbol": i.symbol,
                    "score": round(i.score, 1),
                    "close": round(i.last_close, 2),
                    "below high %": round(i.pct_below_high, 1),
                    "above low %": round(i.pct_above_low, 1),
                    "return %": round(i.return_pct, 1),
                    "why": "; ".join(i.reasoning[:2]),
                }
                for i in screen.items
            ],
            hide_index=True,
            width="stretch",
        )
        c1, _ = st.columns([2, 3])
        target = c1.selectbox(
            "Open in Research",
            [i.symbol for i in screen.items],
            key="forecast_screen_open",
        )
        c1.button(
            "Research this ticker",
            key="forecast_screen_go",
            on_click=_open_research,
            args=(target,),
        )
    else:
        st.info("No symbols matched this screen.")
    st.caption(screen.note)


def render_section(
    section, vault, snapshot_prefix, outcome_prefix, client_factory=None
):
    st.caption(SECTION_HELP.get(section, ""))
    if section == "Watchlist":
        render_recommendations(vault, client_factory)
    elif section == "Track record":
        render_validation(vault, snapshot_prefix, outcome_prefix)
        render_system(vault, snapshot_prefix, outcome_prefix)
    elif section == "Events":
        render_events(vault, client_factory)
    elif section == "Drilldown":
        render_drilldown()
    elif section == "Screens":
        render_screens()
