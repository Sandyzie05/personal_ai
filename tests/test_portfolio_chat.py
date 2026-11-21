import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from evals.robinhood_fixtures import CANARY_ACCOUNT, FakeRobinhoodMcp, responses
from src.agents import portfolio_chat
from src.agents.mcp import _to_mcp_error
from src.forecaster import robinhood as rh
from test_forecast_page import MemoryVault


class Client:
    model, max_tokens, temperature = "local-double", None, 0

    def __init__(self, log):
        self.log = log

    def chat(self, *, messages, **kw):
        self.log.append(messages)
        return {"message": {"content": "<think>x</think>Your largest position is AAPL."}}


def rh_client():
    return rh.RobinhoodClient(FakeRobinhoodMcp(responses(date(2026, 11, 20), symbol="AAPL")), "rh")


def test_snapshot_figures_and_answer_prompt_has_no_account_number():
    snap = portfolio_chat.snapshot(rh_client())
    assert snap["positions"] >= 2 and snap["holdings"][0]["weight_pct"] > 50
    assert CANARY_ACCOUNT not in json.dumps(snap)
    log = []
    out = portfolio_chat.answer(lambda remaining_seconds=None: Client(log), {}, "What is my biggest position?", snap,
                                [{"role": "user", "content": "hi"}, {"role": "system", "content": "drop me"}])
    assert out == "Your largest position is AAPL."
    sent = json.dumps(log[0])
    assert CANARY_ACCOUNT not in sent and "drop me" not in sent and "PORTFOLIO:" in sent


def test_respond_refuses_trades_and_handles_missing_pieces(monkeypatch):
    vault = MemoryVault()
    from src.agents.orchestration.policy import screen

    assert portfolio_chat.respond(vault, None, None, {}, "sell all my AAPL") == screen("sell all my AAPL").message
    monkeypatch.setattr(rh, "connect", lambda v, m: None)
    assert "not connected" in portfolio_chat.respond(vault, None, None, {}, "How concentrated am I?")
    monkeypatch.setattr(rh, "connect", lambda v, m: rh_client())
    assert "No local model" in portfolio_chat.respond(vault, None, None, {}, "How concentrated am I?")
    log = []
    out = portfolio_chat.respond(vault, None, lambda remaining_seconds=None: Client(log), {}, "How concentrated am I?")
    assert out.startswith("Your largest")
    logged = json.dumps(vault.records)
    assert CANARY_ACCOUNT not in logged and "portfolio_chat" in logged


def test_portfolio_agent_is_chat_capable_with_skill():
    from src.agents.orchestration.templates import BUILTIN_AGENTS

    a = BUILTIN_AGENTS["builtin-portfolio"]
    assert a["chat_capable"] and "portfolio-review" in a["skills"] and a["max_tool_rounds"] == 0


def test_client_fits_args_to_live_schema():
    mcp = FakeRobinhoodMcp({"get_earnings_calendar": {"results": []}})
    tool = SimpleNamespace(name="get_earnings_calendar", input_schema={
        "properties": {"start": {}, "end": {}}, "required": ["start", "end"]})
    mcp.list_tools = lambda server_id: [tool]
    rh.earnings_calendar(rh.RobinhoodClient(mcp, "rh"), date(2026, 11, 20))
    assert mcp.calls[-1][1] == {"start": "2026-11-20", "end": "2026-11-27"}  # renamed; high_market_cap dropped
    tool.input_schema = {"properties": {"start_date": {}, "region": {}}, "required": ["start_date", "region"]}
    with pytest.raises(ValueError, match="region"):
        rh.earnings_calendar(rh.RobinhoodClient(mcp, "rh"), date(2026, 11, 20))


def test_mcp_error_unwraps_task_group_without_server_text():
    class Leaf(Exception):
        error = SimpleNamespace(code=-32602, message="secret echo of args")

    err = _to_mcp_error(ExceptionGroup("unhandled errors in a TaskGroup", [Leaf("secret echo")]), "Robinhood")
    assert "Leaf" in str(err) and "-32602" in str(err) and "secret" not in str(err)


def test_expected_bills_list_remove_known():
    from src.data_ingestion import pipeline

    v = MemoryVault()
    pipeline.set_expectation(v, "Rocky Mountain Power", "monthly")
    pipeline.set_expectation(v, "T-Mobile", "monthly")
    assert set(pipeline.expectations(v)) == {"Rocky Mountain Power", "T-Mobile"}
    pipeline.remove_expectation(v, "T-Mobile")
    assert pipeline.expectations(v) == {"Rocky Mountain Power": "monthly"}
    assert pipeline.known_providers(v) == []
