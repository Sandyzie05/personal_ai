"""Trader suite (Track T, S21-S23): advice only, deterministic, bounded, rule-gated. Synthetic bars only."""
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from src import config
from src.trader import risk, timemachine
from src.trader.bars import NY, Bar
from src.trader.pipeline import analyze

ORDER_WORDS = re.compile(r"place_stock_order|order_class|time_in_force|take_profit|stop_loss|bracket|ticket|buying_power|account_number", re.I)
FORBIDDEN_IMPORT = re.compile(r"^\s*(?:from|import)\s+\S*(?:mcp|robinhood|broker|alpaca)", re.I | re.M)


def _day(d, closes, base=100.0):
    t0 = datetime.strptime(d + " 09:30", "%Y-%m-%d %H:%M").replace(tzinfo=NY)
    out, prev = [], base
    for i, c in enumerate(closes):
        out.append(Bar(t0 + timedelta(minutes=5 * i), prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c, 1000))
        prev = c
    return out


def _history(n=15, seed=1):
    rnd, out = random.Random(seed), []
    for i in range(n):
        d = (datetime(2026, 1, 5) + timedelta(days=i)).date()
        if d.weekday() > 4:
            continue
        px, closes = 100.0, []
        for _ in range(78):
            px *= 1 + rnd.gauss(0, 0.001)
            closes.append(px)
        out += _day(d.isoformat(), closes)
    return out


def _scenarios():
    hist = _history()
    flat = [100.0] * 3
    return {
        "triggered": hist + _day("2026-02-02", flat + [100.05, 101.0] + [101.0] * 6),
        "waiting": hist + _day("2026-02-02", [100.0, 100.1]),
        "no_breakout": hist + _day("2026-02-02", [100.0] * 40),
        "no_data": [],
    }


def run_shape() -> dict:
    """S21/S22: no order/broker shape in any plan, and no trader source imports a broker/MCP client."""
    bad, checked = [], 0
    for name, bars in _scenarios().items():
        blob = json.dumps(analyze("AAA", bars, seed=3, draws=200).to_api())
        checked += 1
        if ORDER_WORDS.search(blob.replace("No order is placed or built", "")):
            bad.append(f"output:{name}")
    for f in Path(__file__).parents[1].joinpath("src/trader").glob("*.py"):
        checked += 1
        if FORBIDDEN_IMPORT.search(f.read_text()):
            bad.append(f"import:{f.name}")
    return {"violations": len(bad), "checked": checked, "detail": bad}


def run_simulation() -> dict:
    """S23: same seed -> identical odds, different seed -> different, draws capped by TRADER_MC_MAX_DRAWS."""
    hist = {b.t.date(): [] for b in _history()}
    for b in _history():
        hist[b.t.date()].append(b)
    plan = {"entry": 100.0, "stop": 99.5, "target": 100.5, "risk": 0.5}
    at = datetime(2026, 2, 2, 10, 15, tzinfo=NY)
    a = timemachine.simulate(hist, plan, at, seed=5, draws=300)
    checks = [
        a == timemachine.simulate(hist, plan, at, seed=5, draws=300),
        a != timemachine.simulate(hist, plan, at, seed=6, draws=300),
        timemachine.simulate(hist, plan, at, seed=5, draws=config.TRADER_MC_MAX_DRAWS * 10)["draws"] == config.TRADER_MC_MAX_DRAWS,
        abs(a["target_first"] + a["stop_first"] + a["neither"] - 1) < 0.01,
    ]
    return {"passed": sum(checks), "total": len(checks)}


def run_risk_golden() -> dict:
    """Golden rows: (entry, stop, target, qty, hour, minute, expected verdict). Limits come from config, never tuned here."""
    risk_ps = 6.0
    over = int(config.TRADER_MAX_LOSS_USD // risk_ps) + 1
    ok = int(config.TRADER_MAX_LOSS_USD // risk_ps)
    rows = [
        (100, 100 - risk_ps, 100 + 2 * risk_ps, ok, 10, 15, "PASS"),
        (100, 100 - risk_ps, 100 + 2 * risk_ps, over, 10, 15, "BLOCK"),  # loss over the limit
        (100, 100 - risk_ps, 100 + 0.5 * risk_ps, ok, 10, 15, "BLOCK"),  # reward:risk under minimum
        (100, 100 - risk_ps, 100 + 2 * risk_ps, ok, 9, 40, "BLOCK"),  # before the entry window
        (100, 101, 112, 1, 10, 15, "BLOCK"),  # stop above entry
    ]
    passed = sum(
        risk.check(e, s, t, q, datetime(2026, 1, 5, h, m, tzinfo=NY))["verdict"] == want for e, s, t, q, h, m, want in rows
    )
    return {"passed": passed, "total": len(rows)}
