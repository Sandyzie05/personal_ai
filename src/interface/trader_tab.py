"""Forecast -> Trader: advice-only day-trade plan (docs/trader.md). No order is built or sent (S21)."""
import asyncio
import re

import streamlit as st

from src import config
from src.trader.pipeline import DISCLAIMER, analyze

TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")


VIEWS = ("Plan", "Paper agents")


def render_trader(providers, vault=None, client_factory=None):
    view = st.segmented_control("Trader view", VIEWS, default="Plan", key="trader_view") or "Plan"
    if view == "Paper agents":
        from src.interface.trader_paper_tab import render_paper

        return render_paper(providers, vault, client_factory)
    render_plan(providers)


def render_plan(providers):
    st.warning(DISCLAIMER)
    symbol = st.text_input("Public US ticker", key="trader_symbol", placeholder="TSLA").strip().upper()
    c1, c2, c3, c4 = st.columns(4)
    max_loss = c1.number_input("Max loss per trade ($)", 1.0, 100000.0, config.TRADER_MAX_LOSS_USD, key="trader_max_loss")
    budget = c2.number_input("Budget ($)", 100.0, 10_000_000.0, config.TRADER_BUDGET_USD, key="trader_budget")
    stop = c3.selectbox("Stop", ("or-mid", "or-low"), key="trader_stop", help="Opening-range midpoint or low")
    target_r = c4.selectbox("Target (R)", (2.0, 1.0), key="trader_target")
    if st.button("Plan trade", type="primary"):
        if not TICKER.match(symbol):
            st.error("Enter a US ticker like TSLA.")
        else:
            try:
                with st.spinner("Fetching 60 days of 5-minute bars (free, unofficial Yahoo data)…"):
                    bars = asyncio.run(providers().intraday(symbol))
                st.session_state["trader_plan"] = analyze(symbol, bars, stop, target_r, max_loss, budget)
            except Exception:
                st.session_state["trader_plan"] = None
                st.error("Intraday data unavailable for this ticker. Nothing was saved.")
    plan = st.session_state.get("trader_plan")
    if plan is None:
        st.caption("Nothing planned yet.")
        return
    st.subheader(f"{plan.symbol}: opening-range breakout is {plan.state}")
    if plan.reason:
        st.caption(plan.reason)
    if plan.or_high is not None:
        st.text(f"Opening range {plan.or_low:.2f} - {plan.or_high:.2f} (first 15 minutes, New York)")
    if plan.state != "triggered" or plan.qty is None:
        return
    cols = st.columns(4)
    for col, (label, val) in zip(
        cols,
        (("Entry", plan.entry), ("Stop", plan.stop), ("Target", plan.target), ("Shares", plan.qty)),
    ):
        col.metric(label, f"{val:.2f}" if isinstance(val, float) else str(val))
    st.text(
        f"Sized by {plan.sized_by}. Max loss at the stop ${plan.max_loss:.2f} (a stop can fill worse). "
        f"Reward:risk {plan.reward_risk:.2f}."
    )
    (st.success if plan.verdict == "PASS" else st.error)(f"Risk check: {plan.verdict}")
    for r in plan.reasons:
        st.text(r)
    if plan.odds:
        o, h = plan.odds, plan.history or {}
        st.subheader("Time machine (simulation, not a prediction)")
        st.table(
            [
                {"outcome": "target first", "share": f"{o['target_first']:.1%}"},
                {"outcome": "stop first", "share": f"{o['stop_first']:.1%}"},
                {"outcome": "neither by the close", "share": f"{o['neither']:.1%}"},
            ]
        )
        st.text(f"{o['draws']} draws, seed {o['seed']}: {plan.lean}.")
        if h.get("fired"):
            st.text(f"Same rule on {h['fired']} past sessions: {h['target']} hit target, {h['stop']} hit stop, {h['neither']} neither.")
    else:
        st.caption("Odds unavailable: too little history for this time of day.")
