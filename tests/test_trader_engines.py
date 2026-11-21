import random
from datetime import datetime, timedelta

import pytest

from src.trader import entry, exit_plan, risk, scan, sizing, timemachine
from src.trader.bars import NY, Bar, by_session, parse_yahoo_5m


def day_bars(d, closes, start="09:30", base=100.0):
    t0 = datetime.strptime(d + " " + start, "%Y-%m-%d %H:%M").replace(tzinfo=NY)
    out, prev = [], base
    for i, c in enumerate(closes):
        out.append(Bar(t0 + timedelta(minutes=5 * i), prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c, 1000))
        prev = c
    return out


def synthetic_history(days=20, seed=1):
    rnd = random.Random(seed)
    hist = {}
    for i in range(days):
        d = (datetime(2026, 1, 5) + timedelta(days=i)).date()
        if d.weekday() > 4:
            continue
        closes, px = [], 100.0
        for _ in range(78):
            px *= 1 + rnd.gauss(0, 0.001)
            closes.append(px)
        hist[d] = day_bars(d.isoformat(), closes)
    return hist


FLAT = [100.0] * 3  # opening range 99.9 - 100.1 (three 5m bars)


def test_entry_waiting_until_range_ends():
    assert entry.check(day_bars("2026-01-05", [100, 100.2]))["state"] == "waiting"


def test_entry_triggers_on_close_above_range_high():
    res = entry.check(day_bars("2026-01-05", FLAT + [100.05, 101.0]))
    assert res["state"] == "triggered" and res["entry"] == 101.0 and res["index"] == 4


def test_entry_ignores_wick_without_close():
    day = day_bars("2026-01-05", FLAT + [100.0, 100.0])
    assert entry.check(day)["state"] == "waiting"


def test_entry_expires_late():
    day = day_bars("2026-01-05", [100.0] * 40)  # runs past 11:30 with no breakout
    assert entry.check(day)["state"] == "expired"


def test_exit_plan_math_and_refusals():
    p = exit_plan.plan(364.18, 354.47, 362.18)
    assert p["stop"] == 358.32 and p["target"] == 375.9
    with pytest.raises(ValueError):
        exit_plan.plan(100, 101, 102)  # stop at/above entry
    with pytest.raises(ValueError):
        exit_plan.plan(100, 99, 101, stop="nope")


def test_sizing_limits_and_refusal():
    s = sizing.shares(100.0, 94.0, max_loss=50, budget=5000, max_pos_pct=60)
    assert s["qty"] == 8 and s["limiter"] == "max loss" and s["max_loss"] == 48.0
    with pytest.raises(ValueError):
        sizing.shares(100.0, 40.0, max_loss=50)  # stop too far


def test_risk_pass_block_and_window():
    at = datetime(2026, 1, 5, 10, 15, tzinfo=NY)
    assert risk.check(100, 94, 112, 8, at)["verdict"] == "PASS"
    assert risk.check(100, 94, 112, 9, at)["verdict"] == "BLOCK"  # $54 > $50
    assert risk.check(100, 94, 103, 8, at)["verdict"] == "BLOCK"  # reward:risk 0.5
    early = datetime(2026, 1, 5, 9, 40, tzinfo=NY)
    assert "window" in " ".join(risk.check(100, 94, 112, 8, early)["reasons"])
    assert risk.check(100, 101, 112, 8, at)["verdict"] == "BLOCK"


def test_timemachine_deterministic_by_seed_and_capped():
    hist = synthetic_history()
    plan = {"entry": 100.0, "stop": 99.5, "target": 100.5, "risk": 0.5}
    at = datetime(2026, 1, 30, 10, 15, tzinfo=NY)
    a = timemachine.simulate(hist, plan, at, seed=7, draws=500)
    assert a == timemachine.simulate(hist, plan, at, seed=7, draws=500)
    assert a != timemachine.simulate(hist, plan, at, seed=8, draws=500)
    assert abs(a["target_first"] + a["stop_first"] + a["neither"] - 1) < 0.01


def test_timemachine_draw_cap(monkeypatch):
    from src import config

    monkeypatch.setattr(config, "TRADER_MC_MAX_DRAWS", 50)
    hist = synthetic_history(5)
    plan = {"entry": 100.0, "stop": 99.0, "target": 101.0, "risk": 1.0}
    out = timemachine.simulate(hist, plan, datetime(2026, 1, 30, 10, 15, tzinfo=NY), draws=10**6)
    assert out["draws"] == 50


def test_timemachine_needs_history():
    with pytest.raises(ValueError):
        timemachine.simulate({}, {"entry": 1, "stop": 0.9, "target": 1.1, "risk": 0.1}, datetime(2026, 1, 5, 10, 15, tzinfo=NY))


def test_replay_counts_target_and_stop():
    up = day_bars("2026-01-05", FLAT + [101.0, 103.0, 106.0] + [106.0] * 10)
    down = day_bars("2026-01-06", FLAT + [101.0, 99.0, 96.0] + [96.0] * 10)
    hist = {up[0].t.date(): up, down[0].t.date(): down}
    plan_for = lambda e: exit_plan.plan(e["entry"], e["or_low"], e["or_high"], target_r=1.0)
    rule = lambda d: next(iter([x for x in [entry.check(d)] if x["state"] == "triggered"]), None)
    r = timemachine.replay(hist, rule, plan_for)
    assert (r["fired"], r["target"], r["stop"]) == (2, 1, 1)


def test_daily_setups():
    closes = [100.0] * 25 + [105.0]
    assert "breakout20" in scan.daily_setups(closes)
    assert scan.daily_setups([100.0] * 10) == []
    falling = [100 - i for i in range(30)]
    assert "oversold" in scan.daily_setups(falling)


def test_orb_scan_flags_only_triggered():
    hit = day_bars("2026-01-05", FLAT + [101.0])
    miss = day_bars("2026-01-05", FLAT + [100.0])
    assert list(scan.orb_scan({"AAA": hit, "BBB": miss})) == ["AAA"]


def test_parse_yahoo_fail_closed():
    ts = int(datetime(2026, 1, 5, 10, 0, tzinfo=NY).timestamp())
    q = {"open": [1.0, None], "high": [1.0, 1], "low": [1.0, 1], "close": [1.0, 1], "volume": [5, 5]}
    payload = {"chart": {"result": [{"meta": {"symbol": "TSLA"}, "timestamp": [ts, ts + 300], "indicators": {"quote": [q]}}]}}
    assert len(parse_yahoo_5m(payload, "TSLA")) == 1  # None row skipped
    with pytest.raises(ValueError):
        parse_yahoo_5m(payload, "AAPL")
    q["volume"] = [5]
    with pytest.raises(ValueError):
        parse_yahoo_5m(payload, "TSLA")


def test_intraday_uses_allowlisted_route_and_parses():
    import asyncio

    from src.forecaster.providers import PublicDataProviders

    ts = int(datetime(2026, 1, 5, 10, 0, tzinfo=NY).timestamp())
    q = {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0], "volume": [5]}
    payload = {"chart": {"result": [{"meta": {"symbol": "TSLA"}, "timestamp": [ts], "indicators": {"quote": [q]}}]}}
    calls = []

    async def transport(source, url, params, headers):
        calls.append((source, params))
        return payload

    bars = asyncio.run(PublicDataProviders(transport=transport).intraday("tsla"))
    assert calls == [("yahoo-chart", {"range": "60d", "interval": "5m"})] and len(bars) == 1
