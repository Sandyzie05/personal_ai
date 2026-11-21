"""Backtest / forward runs for the paper agents. Market data is fetched and the ledger written on the calling thread."""
import asyncio
from datetime import datetime, timezone

from src import config
from src.trader import agents, paper
from src.trader.strategies import DEFAULT_PLAN, daily_signals, orb_signals


def signals_for(providers, symbols, strategies) -> tuple[list, list]:
    sigs, errors = [], []
    for sym in symbols[: config.TRADER_MAX_SYMBOLS]:
        try:
            if "orb" in strategies:
                sigs += orb_signals(sym, asyncio.run(providers.intraday(sym)))
            daily = [s for s in strategies if s != "orb"]
            if daily:
                bars = asyncio.run(providers.daily_history(sym))
                for s in daily:
                    sigs += daily_signals(sym, bars, s)
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__}")
    return sigs, errors


def run(vault, providers, client_factory, symbols, strategies, mode="backtest", progress=None, today=None) -> dict:
    from src.agents.slots import agent_for, log_run

    sigs, errors = signals_for(providers, symbols, strategies)
    if mode == "forward":  # only the newest session per strategy/symbol: decided without the benefit of hindsight
        latest = {}
        for s in sigs:
            latest[(s["strategy"], s["symbol"])] = max(latest.get((s["strategy"], s["symbol"]), ""), s["date"])
        sigs = [s for s in sigs if s["date"] == latest[(s["strategy"], s["symbol"])]]
    sigs.sort(key=lambda s: s["at"], reverse=True)  # newest first: the call budget covers recent signals
    book = paper.load(vault)
    roster = {s: agent_for(vault, agents.SLOT_IDS[s]) for s in strategies}
    stats = {"signals": len(sigs), "calls": 0, "invalid": 0, "cached": 0, "not_reviewed": 0, "data_errors": errors}
    for n, sig in enumerate(sigs):
        if progress:
            progress(n / max(len(sigs), 1))
        old = book["trades"]
        mode_of = lambda acct: old.get(f"{sig['id']}:{acct}", {}).get("mode", mode)
        rule, r = DEFAULT_PLAN[sig["strategy"]]
        base = {"action": "take", "stop_rule": rule, "target_r": r, "decided_by": "rule"}
        old[f"{sig['id']}:baseline"] = paper.execute(sig, "baseline", base, mode_of("baseline"))
        prev = old.get(f"{sig['id']}:agent")
        if prev and prev.get("action"):
            choice = {k: prev[k] for k in ("action", "stop_rule", "target_r", "predicted", "confidence", "reason", "model_id")}
            stats["cached"] += 1
        elif client_factory is not None and stats["calls"] < config.TRADER_AGENT_MAX_CALLS:
            agent = roster[sig["strategy"]]
            decision, meta = agents.decide(client_factory, agent, sig)
            stats["calls"] += 1
            log_run(vault, agents.SLOT_IDS[sig["strategy"]], meta["agent_id"], meta["status"], meta.get("latency_ms"),
                    meta.get("invalid_reason"))
            if decision is None:
                stats["invalid"] += 1
                continue
            choice = decision | {"model_id": meta.get("model_id")}
        else:
            stats["not_reviewed"] += 1
            continue
        old[sig["id"] + ":agent"] = paper.execute(sig, "agent", choice | {"decided_by": "agent"}, mode_of("agent"))
    book["runs"].append({"at": datetime.now(timezone.utc).isoformat(), "mode": mode, "symbols": list(symbols)[:10],
                         "strategies": list(strategies), **{k: v for k, v in stats.items() if k != "data_errors"},
                         "data_errors": len(errors)})
    paper.save(vault, book, today)
    return stats
