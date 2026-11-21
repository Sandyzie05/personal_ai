"""One ticker end to end: entry timer -> exit plan -> sizing -> risk label -> odds. Advice only (S21): the result has
no order/ticket fields, `TraderPlan` forbids extras, and nothing here imports a broker or MCP client."""
from typing import Literal, Optional

from src.forecaster.models import Contract
from src.trader import entry, exit_plan, risk, sizing, timemachine
from src.trader.bars import Bar, by_session

DISCLAIMER = (
    "Research and planning only, not financial advice. No order is placed or built. Odds are a simulation from "
    "recent bars; the future can do things they never did."
)


class TraderPlan(Contract):
    symbol: str
    state: Literal["waiting", "triggered", "expired", "no_data"]
    reason: Optional[str] = None
    or_low: Optional[float] = None
    or_high: Optional[float] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    risk_per_share: Optional[float] = None
    qty: Optional[int] = None
    sized_by: Optional[str] = None
    max_loss: Optional[float] = None
    reward_risk: Optional[float] = None
    verdict: Optional[Literal["PASS", "BLOCK"]] = None
    reasons: tuple[str, ...] = ()
    odds: Optional[dict] = None
    history: Optional[dict] = None
    lean: Optional[str] = None
    disclaimer: str = DISCLAIMER


def analyze(symbol: str, bars: list[Bar], stop="or-mid", target_r=2.0, max_loss=None, budget=None, seed=None, draws=None) -> TraderPlan:
    days = by_session(bars)
    if len(days) < 2:
        return TraderPlan(symbol=symbol, state="no_data", reason="need at least 2 sessions of 5-minute bars")
    today = days[max(days)]
    past = {d: b for d, b in days.items() if d != max(days)}
    res = entry.check(today)
    if res["state"] != "triggered":
        return TraderPlan(
            symbol=symbol, state=res["state"], reason=res.get("reason"), or_low=res.get("or_low"), or_high=res.get("or_high")
        )
    try:
        plan = exit_plan.plan(res["entry"], res["or_low"], res["or_high"], stop, target_r)
        size = sizing.shares(plan["entry"], plan["stop"], max_loss, budget)
    except ValueError as e:
        return TraderPlan(symbol=symbol, state="triggered", reason=str(e), entry=res["entry"], or_low=res["or_low"], or_high=res["or_high"])
    verdict = risk.check(plan["entry"], plan["stop"], plan["target"], size["qty"], res["at"])
    odds = history = lean = None
    try:
        odds = timemachine.simulate(past, plan, res["at"], seed, draws)
        lean = timemachine.verdict(odds, target_r)
        history = timemachine.replay(
            past,
            lambda d: (lambda r: r if r["state"] == "triggered" else None)(entry.check(d)),
            lambda e: exit_plan.plan(e["entry"], e["or_low"], e["or_high"], stop, target_r),
        )
    except ValueError:
        pass  # not enough history for this time of day: plan still shown, odds absent
    return TraderPlan(
        symbol=symbol, state="triggered", or_low=res["or_low"], or_high=res["or_high"], entry=plan["entry"], stop=plan["stop"],
        target=plan["target"], risk_per_share=plan["risk"], qty=size["qty"], sized_by=size["limiter"], max_loss=verdict["max_loss"],
        reward_risk=verdict["reward_risk"], verdict=verdict["verdict"], reasons=tuple(verdict["reasons"]), odds=odds,
        history=history, lean=lean,
    )
