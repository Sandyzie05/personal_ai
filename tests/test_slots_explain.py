import json
from types import SimpleNamespace

from src.agents import slots, store
from src.agents.orchestration.templates import _COMMON
from src.forecaster.explain import explain_leans


class Vault:
    def __init__(self):
        self.data = {}

    def store_data(self, key, value, encrypt=True):
        self.data[key] = value
        return True

    def retrieve_data(self, key):
        return self.data.get(key)


def test_slot_clamps_tools_and_keeps_safety_rules_last():
    vault = Vault()
    store.update_agent(vault, "builtin-critic-event", {**store.get_agent(vault, "builtin-critic-event"),
                                                       "extra_instructions": "Focus on filings."})
    agent = slots.agent_for(vault, "forecast.critic.event")
    assert agent["mcp_server_ids"] == [] and agent["max_tool_rounds"] == 0
    assert "Focus on filings." in agent["instructions"] and agent["instructions"].endswith(_COMMON)
    assert "Missing evidence is not neutral" in agent["instructions"]  # forecast-critic skill folded in
    assert set(slots.critic_agents(vault)) == {"macro", "valuation", "event", "downside"}


class Client:
    max_tokens = None
    model = "m"
    temperature = 0.0

    def __init__(self, reply):
        self.reply = reply

    def chat(self, **kwargs):
        return {"message": {"content": json.dumps(self.reply)}}


def _lean(symbol):
    return SimpleNamespace(symbol=symbol, lean="neutral", confidence=0.31, risk_level="moderate",
                           reasoning=("Trend sideways",), status="completed")


def test_explainer_rejects_invented_numbers_and_symbols():
    agent = {"temperature": 0.0, "instructions": ""}
    ok, status, _ = explain_leans(Client({"items": [{"symbol": "AAPL", "note": "Sideways, confidence 0.31."}]}),
                                  agent, [_lean("AAPL")])
    assert status == "completed" and ok.items[0].symbol == "AAPL"
    _, status, reason = explain_leans(Client({"items": [{"symbol": "AAPL", "note": "Target 250."}]}),
                                      agent, [_lean("AAPL")])
    assert (status, reason) == ("invalid", "invented_number")
    _, status, reason = explain_leans(Client({"items": [{"symbol": "TSLA", "note": "Sideways."}]}),
                                      agent, [_lean("AAPL")])
    assert (status, reason) == ("invalid", "unknown_evidence")


def test_thesis_explainer_cites_only_thesis_evidence():
    from src.forecaster.explain import explain_thesis

    thought = SimpleNamespace(scenario="bullish", driver="trend", evidence_ids=("ev1",))
    pick = SimpleNamespace(thought=thought, score=0.82, components={"support": 0.9})
    tree = SimpleNamespace(selected_id="n1", runner_up_id=None, margin=0.3, node=lambda _id: pick)
    card = SimpleNamespace(evidence_id="ev1", source_id="yahoo-chart", title="Prices", summary="Up 4 of 5 sessions")
    snapshot = SimpleNamespace(symbol="AAPL", evidence=(card,))
    good = {"summary": "Trend led with score 0.82.", "what_would_change": "A weak close.", "evidence_ids": ["ev1"]}
    out, status, _ = explain_thesis(Client(good), {"instructions": ""}, snapshot, tree)
    assert status == "completed" and out.evidence_ids == ("ev1",)
    _, status, reason = explain_thesis(Client({**good, "evidence_ids": ["evx"]}), {}, snapshot, tree)
    assert (status, reason) == ("invalid", "unknown_evidence")
