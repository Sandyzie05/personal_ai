"""Seeded Monte Carlo + history replay for one plan. A simulation, not a prediction (S23)."""
import random
from collections import Counter

from src import config
from src.trader.bars import Bar, hhmm


def _outcome(path, stop, target):
    """First of stop/target hit by (high, low) bars; both in one bar counts as stop first (conservative)."""
    for hi, lo in path:
        if lo <= stop:
            return "stop"
        if hi >= target:
            return "target"
    return "neither"


def _slot(b: Bar):
    return b.t.strftime("%H:%M")


def simulate(history: dict, plan: dict, at, seed=None, draws=None) -> dict:
    """history: past sessions {date: [Bar]}. Each future 5m bar is drawn from the same time slot of a past session as
    (high, low, close) returns relative to that bar's open, applied to the simulated previous close."""
    seed = config.TRADER_MC_SEED if seed is None else seed
    draws = min(config.TRADER_MC_DRAWS if draws is None else draws, config.TRADER_MC_MAX_DRAWS)
    slots: dict[str, list] = {}
    for day in history.values():
        for b in day:
            slots.setdefault(_slot(b), []).append((b.h / b.o - 1, b.l / b.o - 1, b.c / b.o - 1))
    flat = hhmm(config.TRADER_FLAT_BY)
    future = sorted(k for k in slots if at.strftime("%H:%M") < k <= flat.strftime("%H:%M"))
    if not future or not slots.get(future[0]):
        raise ValueError("not enough history for this time of day")
    rnd = random.Random(seed)
    tally, close_r = Counter(), 0.0
    for _ in range(draws):
        px, path = plan["entry"], []
        for k in future:
            hr, lr, cr = rnd.choice(slots[k])
            path.append((px * (1 + hr), px * (1 + lr)))
            px *= 1 + cr
        res = _outcome(path, plan["stop"], plan["target"])
        tally[res] += 1
        if res == "neither":
            close_r += (px - plan["entry"]) / plan["risk"]
    n = max(draws, 1)
    return {
        "draws": draws,
        "seed": seed,
        "target_first": round(tally["target"] / n, 3),
        "stop_first": round(tally["stop"] / n, 3),
        "neither": round(tally["neither"] / n, 3),
        "avg_r_if_open": round(close_r / tally["neither"], 2) if tally["neither"] else 0.0,
    }


def replay(history: dict, rule, plan_for) -> dict:
    """Re-run the same rule on each past session: rule(day) -> entry dict or None; plan_for(entry) -> plan dict."""
    tally = Counter()
    for day in history.values():
        e = rule(day)
        if not e:
            continue
        p = plan_for(e)
        after = [(b.h, b.l) for b in day[e["index"] + 1 :] if b.t.time() <= hhmm(config.TRADER_FLAT_BY)]
        tally[_outcome(after, p["stop"], p["target"])] += 1
    return {"fired": sum(tally.values()), "target": tally["target"], "stop": tally["stop"], "neither": tally["neither"]}


def verdict(sim: dict) -> str:
    edge = sim["target_first"] - sim["stop_first"]
    if abs(edge) < 0.05:
        return "close to a coin flip"
    return "odds lean to the target" if edge > 0 else "odds lean to the stop"
