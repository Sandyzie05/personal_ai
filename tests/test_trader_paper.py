import json
import random
from datetime import date, datetime, timedelta

import pytest

from src import config
from src.trader import agents, paper, performance, runner
from src.trader.bars import NY, Bar
from src.trader.strategies import daily_signals, orb_signals
from test_forecast_page import MemoryVault
from test_trader_engines import day_bars, synthetic_history


def daily_bars(n=260, seed=2, start=date(2025, 11, 3)):
    rnd, px, out, d = random.Random(seed), 100.0, [], start
    while len(out) < n:
        if d.weekday() < 5:
            o = px
            px *= 1 + rnd.gauss(0.0005, 0.02)
            out.append(Bar(datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY), o, max(o, px) * 1.01, min(o, px) * 0.99, px, 1e6))
        d += timedelta(days=1)
    return out


def orb_bars():
    hist = [b for d in synthetic_history(12).values() for b in d]
    return hist + day_bars("2026-02-02", [100.0] * 3 + [100.05, 101.0] + [102.5] * 80)


class Providers:
    def __init__(self):
        self.daily, self.intra = daily_bars(), orb_bars()

    async def intraday(self, s):
        return self.intra

    async def daily_history(self, s):
        return self.daily


class Client:
    model = "offline-double"
    max_tokens = None

    def __init__(self, reply=None, log=None):
        self.reply, self.log = reply, log

    def chat(self, *, messages, **kw):
        if self.log is not None:
            self.log.append(messages)
        r = self.reply if self.reply is not None else {"action": "take", "stop_rule": "or-mid" if "STRATEGY:orb" in messages[1]["content"] else "atr2",
                           "target_r": 2.0, "predicted": "target", "confidence": 0.6, "reason": "clean_setup"}
        return {"message": {"content": r if isinstance(r, str) else json.dumps(r)}}


def factory(reply=None, log=None):
    return lambda remaining_seconds=None: Client(reply, log)


def test_daily_signals_have_no_lookahead():
    bars = daily_bars()
    sigs = daily_signals("AAA", bars, "breakout20")
    assert sigs
    s = sigs[len(sigs) // 2]
    i = next(k for k, b in enumerate(bars) if b.t.isoformat() == s["at"])
    changed = bars[: i + 1] + [b._replace(c=b.c * 3, h=b.h * 3) for b in bars[i + 1 :]]
    again = next(x for x in daily_signals("AAA", changed, "breakout20") if x["id"] == s["id"])
    assert again["features"] == s["features"]


def test_orb_signal_features_and_prompt_hide_identity():
    s = orb_signals("ZZZQ", orb_bars())[-1]
    p = agents.prompt(s)
    assert "ZZZQ" not in p and "2026" not in p and str(s["entry"]) not in p and "path" not in p
    assert s["complete"] and "sim_target_first" in s["features"]


def test_decision_schema_rejects_extra_fields_free_text_and_bad_rules():
    s = orb_signals("AAA", orb_bars())[-1]
    good = {"action": "take", "stop_rule": "or-mid", "target_r": 2, "predicted": "target", "confidence": 0.7, "reason": "clean_setup"}
    assert agents.decide(factory(good), {}, s)[0]["target_r"] == 2.0
    for bad in (good | {"qty": 500}, good | {"stop_rule": "atr2"}, good | {"target_r": 7}, good | {"reason": "trust me"},
                good | {"confidence": "0.9"}, "Sure! buy 1000 shares now", ""):
        d, meta = agents.decide(factory(bad), {}, s)
        assert d is None and meta["status"] == "invalid"


def test_paper_execute_math_slippage_and_skip():
    s = orb_signals("AAA", orb_bars())[-1]
    take = paper.execute(s, "agent", {"action": "take", "stop_rule": "or-mid", "target_r": 1.0}, "backtest")
    assert take["taken"] and take["outcome"] == "target" and take["risk_verdict"] == "PASS"
    slip = config.TRADER_SLIPPAGE_BPS / 10_000
    assert take["pnl"] == round(take["qty"] * (take["exit"] * (1 - slip) - take["entry"] * (1 + slip)), 2)
    assert take["qty"] * (take["entry"] - take["stop"]) <= config.TRADER_MAX_LOSS_USD
    skip = paper.execute(s, "agent", {"action": "skip", "stop_rule": "or-mid", "target_r": 1.0}, "backtest")
    assert not skip["taken"] and skip["pnl"] == 0 and skip["outcome"] == "target"
    assert not any(w in json.dumps(take) for w in ("order", "ticket", "bracket", "account_number"))


def test_runner_backtest_caches_decisions_and_baseline_always(monkeypatch):
    vault, log = MemoryVault(), []
    st = runner.run(vault, Providers(), factory(log=log), ["AAA"], ["orb", "breakout20"], today=date(2026, 2, 3))
    assert st["calls"] == len(log) == min(st["signals"], config.TRADER_AGENT_MAX_CALLS) and st["invalid"] == 0
    trades = list(paper.load(vault)["trades"].values())
    assert {t["account"] for t in trades} == {"agent", "baseline"}
    assert sum(t["account"] == "baseline" for t in trades) == st["signals"]
    again = runner.run(vault, Providers(), factory(log=log), ["AAA"], ["orb", "breakout20"], today=date(2026, 2, 3))
    assert again["cached"] == st["calls"]  # cached decisions are free; budget extends coverage further back


def test_runner_without_llm_records_baseline_only():
    vault = MemoryVault()
    st = runner.run(vault, Providers(), None, ["AAA"], ["bounce", "breakout20"], today=date(2026, 2, 3))
    assert st["calls"] == 0 and st["not_reviewed"] == st["signals"]
    assert {t["account"] for t in paper.load(vault)["trades"].values()} <= {"baseline"}


def test_forward_mode_only_latest_session():
    vault = MemoryVault()
    st = runner.run(vault, Providers(), factory(), ["AAA"], ["orb"], mode="forward", today=date(2026, 2, 3))
    assert st["signals"] == 1
    assert {t["mode"] for t in paper.load(vault)["trades"].values()} == {"forward"}


def test_retention_prunes_older_than_a_year():
    vault = MemoryVault()
    book = {"trades": {"old": {"date": "2024-01-01"}, "new": {"date": "2026-01-01"}}, "runs": [{"at": "2024-01-01T00:00"}]}
    paper.save(vault, book, today=date(2026, 2, 1))
    assert list(paper.load(vault)["trades"]) == ["new"] and paper.load(vault)["runs"] == []


def test_performance_summary_curve_and_code_verdict():
    vault = MemoryVault()
    runner.run(vault, Providers(), factory(), ["AAA"], ["breakout20"], today=date(2026, 2, 3))
    trades = list(paper.load(vault)["trades"].values())
    rows = {r["account"]: r for r in performance.summarize(trades)}
    assert rows["agent"]["prediction_accuracy"] is not None and rows["baseline"]["prediction_accuracy"] is None
    curve = performance.equity_curve(trades)
    last = [r for r in curve if r["series"] == "breakout20/baseline"][-1]["equity"]
    assert round(last - config.TRADER_PAPER_START_CASH, 2) == rows["baseline"]["pnl"]
    assert performance.code_verdict(rows["agent"], rows["baseline"]) in ("beats_baseline", "matches_baseline", "trails_baseline", "too_few_trades")
    assert sum(m["pnl"] for m in performance.monthly(trades) if m["series"] == "breakout20/baseline") == pytest.approx(rows["baseline"]["pnl"])


def test_reviewer_closed_choices():
    good = {"verdict": "trails_baseline", "suggestion": "skip_more", "confidence": 0.4}
    assert agents.review(factory(good), {}, "orb", {"pnl": 1}, {"pnl": 2})[0] == good
    assert agents.review(factory(good | {"note": "x"}), {}, "orb", {}, {})[0] is None
