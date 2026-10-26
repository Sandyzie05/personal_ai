"""Forecast page sections ported from stock_predictor tabs.

Network happens only inside explicit button handlers. All external text (news
titles/publishers/URLs) is untrusted and rendered with st.text only. Loaded by
path from forecast.py, so import only src.forecaster / src.data_vault here.
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

SECTIONS = ("Research", "Recommendations", "Validation", "Events", "Drilldown & System", "Screens")
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
        source_id, result, at=datetime.now(timezone.utc),
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
            outcome = OutcomeEvaluation.model_validate(vault.retrieve_data(key)["outcome"])
        except (DataVaultError, ValueError, KeyError, TypeError):
            bad += 1
            continue
        seen = outcomes.get(outcome.forecast_id)
        if seen is None or outcome.evaluated_at > seen.evaluated_at:
            outcomes[outcome.forecast_id] = outcome
    pairs = []
    for key in sorted(k for k in vault.list_keys() if k.startswith(snapshot_prefix)):
        try:
            forecast = ForecastRecord.model_validate(vault.retrieve_data(key)["forecast"])
        except (DataVaultError, ValueError, KeyError, TypeError):
            bad += 1
            continue
        pairs.append((forecast, outcomes.get(forecast.forecast_id)))
    return pairs, bad


# --- Recommendations -------------------------------------------------------

async def _market_all(providers, symbols):
    return await asyncio.gather(
        *(providers.market(ResearchRequest(symbol=s)) for s in symbols), return_exceptions=True
    )


def render_recommendations():
    st.subheader("Research leans")
    st.warning(
        "Research lean only: uncalibrated close-only technicals, no sentiment/fundamentals, "
        "no price targets, not a buy/sell instruction or financial advice."
    )
    raw = st.text_input("Public US tickers (comma separated)", value="AAPL, MSFT, NVDA",
                        key="forecast_lean_symbols")
    if st.button("Compute research leans"):
        symbols, rejected = [], []
        for part in dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()):
            try:
                symbols.append(canonical_symbol(part))
            except ValueError:
                rejected.append(part[:8])
        extra = symbols[MAX_LEAN_SYMBOLS:]
        symbols = symbols[:MAX_LEAN_SYMBOLS]
        leans, failed = [], []
        if symbols:
            with st.spinner("Fetching public price history…"):
                start = time.monotonic()
                results = asyncio.run(_market_all(_providers(), symbols))
                ms = (time.monotonic() - start) * 1000
            for symbol, result in zip(symbols, results):
                if isinstance(result, BaseException) or result.value is None:
                    failed.append((symbol, "unavailable", [] if isinstance(result, BaseException)
                                   else [f"{i.code}: {i.message}" for i in result.issues]))
                    continue
                _health().record("yahoo-chart", result, at=datetime.now(timezone.utc), latency_ms=ms)
                leans.append(research_lean(technical_snapshot(result.value)))
        st.session_state["forecast_leans"] = {
            "leans": leans, "failed": failed, "rejected": rejected, "dropped": extra,
        }
    state = st.session_state.get("forecast_leans")
    if not state:
        st.caption("Nothing fetched yet. Leans are computed only when you press the button.")
        return
    max_risk = st.selectbox("Maximum risk level for watchlist", RISKS, index=2)
    ranked = rank_leans(state["leans"])
    min_conf = st.slider(
        "Minimum confidence", 0.0, 0.95, 0.0, 0.05,
        help="Technical-only leans carry low confidence by design (no sentiment evidence).",
    )
    picked = select_diversified(ranked, max_risk=max_risk, min_confidence=min_conf)
    if picked:
        st.write(f"Diversified watchlist ({len(picked)} of {len(ranked)} ranked)")
        st.table([
            {"rank": p.rank, "symbol": p.lean.symbol, "research lean": p.lean.lean,
             "confidence": f"{p.lean.confidence:.2f}", "risk": p.lean.risk_level,
             "overall score": f"{p.lean.overall_score:.1f}"}
            for p in picked
        ])
        for p in picked:
            for reason in p.lean.reasoning:
                st.caption(f"{p.lean.symbol}: {reason}")
    else:
        st.info("No symbol passed the confidence/risk filters (or none had enough evidence).")
    for lean in state["leans"]:
        if lean.status != "completed":
            st.warning(f"{lean.symbol}: insufficient evidence. {' '.join(lean.reasoning)}")
    for symbol, status, notes in state["failed"]:
        st.warning(f"{symbol}: market data {status}. {' | '.join(notes)}")
    if state["rejected"]:
        st.warning("Rejected as not US tickers: " + ", ".join(state["rejected"]))
    if state["dropped"]:
        st.warning(f"Only the first {MAX_LEAN_SYMBOLS} tickers were processed; skipped: "
                   + ", ".join(state["dropped"]))


# --- Validation ------------------------------------------------------------

def render_validation(vault, snapshot_prefix, outcome_prefix):
    st.subheader("Validation scoreboard")
    pairs, bad = load_pairs(vault, snapshot_prefix, outcome_prefix)
    if bad:
        st.warning(f"{bad} saved record(s) could not be read and were excluded; nothing was modified.")
    if not pairs:
        st.info("No saved forecasts yet, so there is nothing to validate. No network is used here.")
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
        st.dataframe(result["daily"])
    else:
        st.caption("No scored days yet (needs evaluated outcomes after target close).")
    with st.container():
        st.write("Trend and baselines")
        st.json({"trend": result["trend"], "baselines": summary["baselines"]}, expanded=False)
    st.download_button(
        "Download validation CSV", data=to_csv(pairs, as_of=now),
        file_name="forecast_validation.csv", mime="text/csv",
    )


# --- Events ----------------------------------------------------------------

def _story_lines(stories):
    for s in stories:
        st.text(f"[{s.sentiment}/{s.directional_bias} impact {s.impact_score:.2f}] "
                f"{s.title} ({s.publisher}, {s.published_at:%Y-%m-%d})")
        if s.url:
            st.text(f"  {s.url}")


def render_events():
    st.subheader("Market events and news")
    st.warning("Headlines are untrusted external text, shown as plain text. Impact scores are heuristic.")
    if st.button("Fetch today's stories"):
        try:
            with st.spinner("Fetching public news…"):
                st.session_state["forecast_news_report"] = _call(
                    "news-service", lambda: NewsService(_providers()).build_today_report())
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
            st.caption(report.note)
            st.write("Top stories")
            _story_lines(report.stories[:10]) if report.stories else st.caption("No stories returned.")
            for title, leans in (("Upward-leaning symbols", report.bullish),
                                 ("Downward-leaning symbols", report.bearish)):
                st.write(title)
                if leans:
                    st.table([{"symbol": x.symbol, "score": f"{x.score:.2f}",
                               "stories": len(x.story_ids)} for x in leans])
                else:
                    st.caption("None identified.")
            st.write("Conflicting signals")
            if report.disagreements:
                st.table([{"symbol": d.symbol, "up": f"{d.up_score:.2f}",
                           "down": f"{d.down_score:.2f}", "stronger": d.stronger_side}
                          for d in report.disagreements])
            else:
                st.caption("No symbols with conflicting stories.")
    with st.form("forecast_news_search"):
        query = st.text_input("Search topic or ticker", key="forecast_news_query")
        go = st.form_submit_button("Search news")
    if go:
        try:
            with st.spinner("Searching public news…"):
                st.session_state["forecast_news_found"] = _call(
                    "news-service", lambda: NewsService(_providers()).search_news(query))
        except Exception:
            st.session_state["forecast_news_found"] = None
            st.error("News search unavailable or invalid query.")
    found = st.session_state.get("forecast_news_found")
    if found is not None:
        _status(found, "News search")
        if found.value is not None:
            _story_lines(found.value) if found.value else st.caption("No stories matched.")


# --- Drilldown & System ----------------------------------------------------

def render_drilldown():
    st.subheader("Technical drilldown")
    symbol = st.text_input("Public US ticker", key="forecast_drill_symbol", placeholder="AAPL")
    if st.button("Load technicals"):
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
    st.write(f"{tech.symbol} · {tech.status} · {tech.bar_count} bars · trend {tech.trend}")
    fields = ("last_price", "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14", "macd",
              "macd_signal", "macd_histogram", "bollinger_upper", "bollinger_lower",
              "support", "resistance", "annualized_volatility_pct", "atr", "avg_volume_20", "volume_ratio")
    st.table([{"indicator": f, "value": "unavailable" if getattr(tech, f) is None
               else f"{getattr(tech, f):.2f}"} for f in fields])
    for item in tech.unavailable:
        st.caption(f"Unavailable: {item}")
    if lean.status == "completed":
        st.write(f"Research lean: {lean.lean} · confidence {lean.confidence:.2f} · risk {lean.risk_level}")
        for reason in lean.reasoning:
            st.caption(reason)
    else:
        st.warning("Research lean: insufficient evidence. " + " ".join(lean.reasoning))
    st.caption(" ".join(lean.limitations))


def render_system(vault, snapshot_prefix, outcome_prefix):
    st.subheader("System health")
    pairs, bad = load_pairs(vault, snapshot_prefix, outcome_prefix)
    snap = _health().snapshot(pairs, as_of=datetime.now(timezone.utc))
    st.write(f"Overall provider status: {snap['status']}")
    if snap["providers"]:
        st.table([{k: p[k] for k in ("source_id", "state", "calls", "failures", "partials",
                                     "last_status", "last_latency_ms")}
                  for p in snap["providers"]])
    else:
        st.info("No provider calls yet this session (status unknown). Health fills in as you use other sections.")
    if bad:
        st.warning(f"{bad} saved forecast/outcome record(s) unreadable.")
    acc = snap["accuracy"]
    st.write(f"Saved forecasts: {acc['total_forecasts']} · evaluated {acc['evaluated']} · "
             f"{acc['directional_accuracy_label']}")


# --- Screens ---------------------------------------------------------------

def render_screens():
    st.subheader("Reference stock screens")
    st.warning("Reference screens over a fixed large-cap list using price history only; not recommendations.")
    kind = st.selectbox("Screen", list(SCREEN_LABELS), format_func=SCREEN_LABELS.get,
                        key="forecast_screen_kind")
    if st.button("Run screen"):
        try:
            with st.spinner("Screening public price history…"):
                st.session_state["forecast_screen"] = _call(
                    "screen-service", lambda: ScreenService(_providers()).screen(kind))
        except Exception:
            st.session_state["forecast_screen"] = None
            st.error("Screen unavailable. Nothing was saved.")
    result = st.session_state.get("forecast_screen")
    if result is None:
        st.caption("Nothing fetched yet. Prices are requested only when you press the button.")
        return
    _status(result, "Screen")
    screen = result.value
    if screen is None:
        return
    st.caption(f"{SCREEN_LABELS.get(screen.kind, screen.kind)} · {screen.window}")
    st.caption(f"Analyzed {screen.symbols_analyzed} of {screen.symbols_requested} symbols.")
    if screen.symbols_failed:
        st.warning("No data for: " + ", ".join(screen.symbols_failed))
    if screen.symbols_rejected:
        st.warning("Rejected symbols: " + ", ".join(screen.symbols_rejected))
    if screen.items:
        st.table([{"rank": i.rank, "symbol": i.symbol, "score": f"{i.score:.1f}",
                   "close": f"{i.last_close:.2f}", "below high %": f"{i.pct_below_high:.1f}",
                   "above low %": f"{i.pct_above_low:.1f}", "return %": f"{i.return_pct:.1f}"}
                  for i in screen.items])
    else:
        st.info("No symbols matched this screen.")
    st.caption(screen.note)


def render_section(section, vault, snapshot_prefix, outcome_prefix):
    if section == "Recommendations":
        render_recommendations()
    elif section == "Validation":
        render_validation(vault, snapshot_prefix, outcome_prefix)
    elif section == "Events":
        render_events()
    elif section == "Drilldown & System":
        render_drilldown()
        render_system(vault, snapshot_prefix, outcome_prefix)
    elif section == "Screens":
        render_screens()
