"""Forecast -> Trader -> Paper agents: AI strategy agents on simulated money, and how they performed (docs/trader.md).
Everything shown is code-computed numbers or closed enums; no model text is rendered (S16, S24)."""
import re

import pandas as pd
import streamlit as st

from src import config
from src.trader import agents, paper, performance, runner
from src.trader.strategies import STRATEGIES

LABELS = {
    "orb": "Opening-range breakout (5-minute bars, last 60 days)",
    "breakout20": "20-day breakout (daily bars, 1 year)",
    "bounce": "Oversold bounce (daily bars, 1 year)",
}
NOTE = (
    "Paper money only: a local simulation from real bars, kept in your encrypted vault. Nothing is sent to a broker. "
    "Each AI agent is compared with a baseline that takes every signal. Not financial advice; past results are not a forecast."
)
TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")
LOG_COLS = ["date", "symbol", "strategy", "account", "mode", "action", "reason", "predicted", "confidence", "outcome",
            "entry", "stop", "target", "qty", "risk_verdict", "exit", "pnl", "r_multiple"]


def _factory(client_factory):
    try:
        return client_factory() if client_factory else None
    except Exception:
        return None


def _run(vault, providers, client_factory, symbols, strategies, mode):
    factory = _factory(client_factory)
    if factory is None:
        st.info("No local model is configured, so only the take-every-signal baseline is recorded.")
    bar = st.progress(0.0, text="Agents deciding on signals…")
    with st.spinner("Fetching bars and running the paper agents…"):
        stats = runner.run(vault, providers(), factory, symbols, strategies, mode, progress=lambda f: bar.progress(f))
    bar.empty()
    st.success(
        f"{stats['signals']} signals: {stats['calls']} new AI decisions, {stats['cached']} reused, "
        f"{stats['invalid']} invalid replies, {stats['not_reviewed']} not reviewed (budget {config.TRADER_AGENT_MAX_CALLS} per run)."
    )
    for e in stats["data_errors"]:
        st.caption(f"Data unavailable: {e}")


def _review(vault, client_factory, rows):
    from src.agents.slots import agent_for, log_run

    factory = _factory(client_factory)
    agent = agent_for(vault, agents.REVIEW_SLOT)
    out = []
    for s in STRATEGIES:
        a = next((r for r in rows if r["strategy"] == s and r["account"] == "agent"), None)
        b = next((r for r in rows if r["strategy"] == s and r["account"] == "baseline"), None)
        if not a or not b:
            continue
        truth = performance.code_verdict(a, b)
        said, meta = agents.review(factory, agent, s, a, b) if factory else (None, {"status": "no model"})
        if factory:
            log_run(vault, agents.REVIEW_SLOT, meta.get("agent_id"), meta["status"], meta.get("latency_ms"), meta.get("invalid_reason"))
        out.append({
            "strategy": s, "numbers say": truth.replace("_", " "),
            "reviewer says": said["verdict"].replace("_", " ") if said else meta["status"],
            "agrees with numbers": (said["verdict"] == truth) if said else None,
            "suggestion": said["suggestion"].replace("_", " ") if said else None,
            "confidence": said["confidence"] if said else None,
        })
    st.session_state["trader_review"] = out


def render_paper(providers, vault, client_factory):
    from src.interface.forecast_tabs import agent_chip

    st.warning(NOTE)
    if vault is None:
        st.info("Unlock the vault to keep a paper ledger.")
        return
    raw = st.text_input("Tickers (up to %d, comma separated)" % config.TRADER_MAX_SYMBOLS, key="paper_symbols", placeholder="TSLA, NVDA")
    symbols = [t.strip().upper() for t in raw.split(",") if t.strip()]
    bad = [t for t in symbols if not TICKER.match(t)]
    strategies = st.multiselect("Agents", STRATEGIES, default=list(STRATEGIES), format_func=LABELS.get, key="paper_strategies")
    for s in strategies:
        agent_chip(vault, agents.SLOT_IDS[s], key=f"paper_{s}")
    c1, c2 = st.columns(2)
    clicked = (
        "backtest" if c1.button("Run backtest (past year)", type="primary") else
        "forward" if c2.button("Run forward session (latest bars)") else None
    )
    if clicked:
        if bad or not symbols or not strategies:
            st.error("Enter 1-%d US tickers like TSLA and pick at least one agent." % config.TRADER_MAX_SYMBOLS)
        else:
            _run(vault, providers, client_factory, symbols[: config.TRADER_MAX_SYMBOLS], strategies, clicked)
    trades = list(paper.load(vault)["trades"].values())
    if not trades:
        st.caption("No paper trades yet. Run a backtest to start the record (kept for %d days)." % config.TRADER_HISTORY_DAYS)
        return
    mode = st.segmented_control("Record", ("All", "Backtest", "Forward"), default="All", key="paper_mode") or "All"
    if mode != "All":
        trades = [t for t in trades if t["mode"] == mode.lower()]
    if not trades:
        st.caption(f"No {mode.lower()} trades yet. A forward session records only the newest session's signals.")
        return
    rows = performance.summarize(trades)
    st.subheader("Leaderboard")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    curve = performance.equity_curve(trades)
    if curve:
        st.subheader("Paper equity (start $%s per account)" % f"{config.TRADER_PAPER_START_CASH:,.0f}")
        df = pd.DataFrame(curve).groupby(["date", "series"])["equity"].last().unstack().ffill()
        st.line_chart(df)
        st.subheader("P&L by month")
        st.bar_chart(pd.DataFrame(performance.monthly(trades)).pivot_table(index="month", columns="series", values="pnl", fill_value=0))
    if st.button("Review agents"):
        _review(vault, client_factory, rows)
    if st.session_state.get("trader_review"):
        st.subheader("Reviewer (checked against the numbers)")
        st.dataframe(pd.DataFrame(st.session_state["trader_review"]), hide_index=True, width="stretch")
    st.subheader("Trade log")
    pick = st.segmented_control("Agent", ("all",) + STRATEGIES, default="all", key="paper_log_strategy") or "all"
    log = sorted((t for t in trades if pick == "all" or t["strategy"] == pick), key=lambda t: t["signal_at"], reverse=True)[:500]
    if not log:
        st.caption("No trades in this record yet.")
        return
    st.dataframe(pd.DataFrame(log, columns=LOG_COLS), hide_index=True, width="stretch")
