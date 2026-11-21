import asyncio
import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from src.agents.orchestration.schemas import EDGES, PlanStep, TraderRequest
from src.agents.orchestration.workers import trader_worker
from src.agents.orchestration.policy import screen
from src.trader.bars import NY
from src.trader.pipeline import TraderPlan, analyze
from tests.test_trader_engines import day_bars, synthetic_history

ORDER_WORDS = ("order", "ticket", "place_stock_order", "bracket", "account", "broker", "buying_power")


def _bars():
    hist = [b for d in synthetic_history(12).values() for b in d]
    today = day_bars("2026-02-02", [100.0] * 3 + [100.05, 101.0] + [101.0] * 6, base=100.0)
    return hist + today


class FakeProviders:
    async def intraday(self, symbol):
        return _bars()


def test_pipeline_plan_has_no_order_shape():
    p = analyze("AAA", _bars(), seed=3, draws=200)
    assert p.state == "triggered" and p.qty and p.verdict in ("PASS", "BLOCK")
    blob = json.dumps(p.to_api()).lower().replace("disclaimer", "")
    assert not [w for w in ORDER_WORDS if w in blob.replace("no order is placed or built", "")]
    with pytest.raises(ValidationError):
        TraderPlan(symbol="AAA", state="waiting", order_type="market")


def test_pipeline_no_data_and_waiting():
    assert analyze("AAA", []).state == "no_data"
    hist = [b for d in synthetic_history(3).values() for b in d]
    waiting = hist + day_bars("2026-02-02", [100.0, 100.1])
    assert analyze("AAA", waiting).state == "waiting"


def test_worker_findings_are_templated_numbers():
    fs = trader_worker(FakeProviders())(PlanStep(id="t", agent="trader", goal="Trader plan", symbol="AAA"), {})
    keys = {f.claim_key for f in fs}
    assert {"trader:AAA:plan", "trader:AAA:odds"} <= keys
    assert all(len(f.summary) < 500 and f.trust == "trusted" for f in fs)


def test_edge_schema_strict():
    assert EDGES[("coordinator", "trader")] is TraderRequest
    assert TraderRequest(symbol="AAA").setup == "orb"
    for bad in ({"symbol": "aaa"}, {"symbol": "AAA", "qty": 5}, {"symbol": "AAA", "stop": "x"}, {"symbol": "AAA", "account": "1"}):
        with pytest.raises(ValidationError):
            TraderRequest(**bad)


def test_policy_allows_research_refuses_orders():
    for q in ("Research day-trade setups for NVDA", "Entry, stop and target levels for TSLA today"):
        assert screen(q) is None
    for q in ("place the bracket order for TSLA", "submit the trade ticket for NVDA"):
        assert screen(q) is not None and screen(q).kind == "trade"
