"""Local paper ledger (simulated fills from real bars). Lives only in the encrypted vault; nothing is sent to any broker
(S21, S24). Sizing uses a fixed per-trade budget so a trade's result never depends on the order runs happened in."""
from datetime import date, timedelta
from typing import Optional

from src import config
from src.agents.orchestration.filelock import locked
from src.agents.store import _load, _save
from src.trader import risk, sizing
from src.trader.strategies import resolve, stop_level

KEY = "trader_paper_v1"
ACCOUNTS = ("agent", "baseline")


def load(vault) -> dict:
    data = _load(vault, KEY, {})
    return {"trades": data.get("trades", {}), "runs": data.get("runs", [])}


def save(vault, book: dict, today: Optional[date] = None) -> None:
    cutoff = ((today or date.today()) - timedelta(days=config.TRADER_HISTORY_DAYS)).isoformat()
    book["trades"] = {k: t for k, t in book["trades"].items() if t["date"] >= cutoff}
    book["runs"] = [r for r in book["runs"] if r["at"][:10] >= cutoff][-200:]
    with locked("trader_paper"):
        _save(vault, KEY, book)


def execute(signal: dict, account: str, choice: dict, mode: str) -> dict:
    """One simulated trade. `choice` = the agent's decision (or the baseline rule). Skipped and blocked signals still get
    a hypothetical outcome so skips and predictions can be scored, but carry zero P&L."""
    e, s = signal["entry"], signal["strategy"]
    t = {
        "id": f"{signal['id']}:{account}", "signal_id": signal["id"], "account": account, "strategy": s,
        "symbol": signal["symbol"], "date": signal["date"], "signal_at": signal["at"], "mode": mode,
        **{k: choice.get(k) for k in ("action", "stop_rule", "target_r", "predicted", "confidence", "reason")},
        "decided_by": choice.get("decided_by", account), "model_id": choice.get("model_id"),
        "entry": e, "qty": 0, "taken": False, "risk_verdict": None, "pnl": 0.0, "r_multiple": None,
    }
    try:
        stop = stop_level(s, choice["stop_rule"], e, signal["levels"])
        if stop >= e:
            raise ValueError("stop not below entry")
    except (ValueError, KeyError):
        return t | {"outcome": "refused", "stop": None, "target": None, "exit": None, "exit_at": None}
    target = round(e + choice["target_r"] * (e - stop), 2)
    outcome, exit_px, exit_at = resolve(signal["path"], e, stop, target, signal["complete"])
    t |= {"stop": stop, "target": target, "outcome": outcome, "exit": exit_px, "exit_at": exit_at}
    if exit_px is not None:
        t["r_multiple"] = round((exit_px - e) / (e - stop), 2)
    if choice["action"] != "take":
        return t
    try:
        size = sizing.shares(e, stop, config.TRADER_MAX_LOSS_USD, config.TRADER_PAPER_START_CASH)
    except ValueError:
        return t | {"risk_verdict": "BLOCK"}
    from datetime import datetime

    v = risk.check(e, stop, target, size["qty"], datetime.fromisoformat(signal["at"]), window=signal["window"])
    t["risk_verdict"] = v["verdict"]
    if v["verdict"] != "PASS":
        return t
    t |= {"qty": size["qty"], "taken": True}
    if exit_px is not None:
        slip = config.TRADER_SLIPPAGE_BPS / 10_000
        t["pnl"] = round(size["qty"] * (exit_px * (1 - slip) - e * (1 + slip)), 2)
        t["r_multiple"] = round(t["pnl"] / (size["qty"] * (e - stop)), 2)  # net of slippage for taken trades
    return t
