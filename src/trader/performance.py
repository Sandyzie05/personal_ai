"""Scoreboard for the paper agents: P&L, win rate, drawdown, prediction accuracy and calibration, per strategy/account."""
from collections import defaultdict

from src import config

CLOSED = ("target", "stop", "neither")


def _series(t):
    return f"{t['strategy']}/{t['account']}"


def equity_curve(trades) -> list[dict]:
    rows, eq = [], defaultdict(lambda: config.TRADER_PAPER_START_CASH)
    for t in sorted((t for t in trades if t["taken"] and t["outcome"] in CLOSED), key=lambda t: t["exit_at"]):
        eq[_series(t)] += t["pnl"]
        rows.append({"date": t["exit_at"][:10], "series": _series(t), "equity": round(eq[_series(t)], 2)})
    return rows


def monthly(trades) -> list[dict]:
    m = defaultdict(float)
    for t in trades:
        if t["taken"] and t["outcome"] in CLOSED:
            m[(t["exit_at"][:7], _series(t))] += t["pnl"]
    return [{"month": k[0], "series": k[1], "pnl": round(v, 2)} for k, v in sorted(m.items())]


def _drawdown(curve):
    peak, dd = config.TRADER_PAPER_START_CASH, 0.0
    for r in curve:
        peak = max(peak, r["equity"])
        dd = max(dd, peak - r["equity"])
    return round(dd, 2)


def summarize(trades) -> list[dict]:
    groups = defaultdict(list)
    for t in trades:
        groups[(t["strategy"], t["account"])].append(t)
    out = []
    for (s, a), ts in sorted(groups.items()):
        taken = [t for t in ts if t["taken"] and t["outcome"] in CLOSED]
        wins = [t for t in taken if t["pnl"] > 0]
        scored = [t for t in ts if t["outcome"] in CLOSED and t.get("predicted")]
        hits = [t for t in scored if t["predicted"] == t["outcome"]]
        skipped = [t for t in ts if t["action"] == "skip" and t["outcome"] in CLOSED]
        pnl = round(sum(t["pnl"] for t in taken), 2)
        out.append({
            "strategy": s, "account": a, "signals": len(ts), "taken": len(taken),
            "open": sum(t["outcome"] == "open" for t in ts), "blocked": sum(t["risk_verdict"] == "BLOCK" for t in ts),
            "win_rate": round(len(wins) / len(taken), 3) if taken else None,
            "pnl": pnl, "return_pct": round(pnl / config.TRADER_PAPER_START_CASH * 100, 2),
            "avg_r": round(sum(t["r_multiple"] for t in taken) / len(taken), 2) if taken else None,
            "max_drawdown": _drawdown([r for r in equity_curve(ts)]),
            "prediction_accuracy": round(len(hits) / len(scored), 3) if a == "agent" and scored else None,
            # Brier score of "my prediction is right" (lower is better; 0.25 = no skill at 50% confidence).
            "brier": round(sum((t["confidence"] - (t["predicted"] == t["outcome"])) ** 2 for t in scored) / len(scored), 3)
            if a == "agent" and scored else None,
            "skip_saved": round(sum(t["outcome"] == "stop" for t in skipped) / len(skipped), 3) if skipped else None,
        })
    return out


def code_verdict(agent: dict, base: dict) -> str:
    """The numbers' own answer, to ground the reviewer agent: it must agree or be flagged."""
    if (agent.get("taken") or 0) + (base.get("taken") or 0) < 5:
        return "too_few_trades"
    diff = agent["pnl"] - base["pnl"]
    tol = 0.01 * config.TRADER_PAPER_START_CASH
    return "beats_baseline" if diff > tol else "trails_baseline" if diff < -tol else "matches_baseline"
