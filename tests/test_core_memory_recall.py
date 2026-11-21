import pathlib

import pytest

from evals.golden import FakeVault
from src.agents.orchestration import memory
from src.agents.orchestration.coordinator import RunReport, dependency_inputs
from src.agents.orchestration.schemas import EDGES, CoreFact, Finding
from src.agents.orchestration.workers import expense_worker, render_report


def _approve(v, key, value, trust="trusted"):
    pid = memory.propose(v, key, value, proposer="test", trust=trust)
    return memory.approve(
        v,
        pid,
        token=memory.issue_approval_token("s", pid),
        session_id="s",
        acknowledge_untrusted=trust == "untrusted",
    )


def test_recall_returns_current_approved_facts_only():
    v = FakeVault()
    _approve(v, "cashflow_delta:2026-Q1", "-40")
    _approve(v, "cashflow_delta:2026-Q1", "-42.5")  # correction supersedes
    gone = _approve(v, "note:gas_autopay", "on")
    memory.forget(
        v,
        gone["id"],
        token=memory.issue_approval_token("s", gone["id"], "forget"),
        session_id="s",
    )
    memory.propose(
        v, "note:pending_only", "x", proposer="test"
    )  # pending is never recalled
    facts = memory.recall(v, "cash flow change 2026 Q1 gas autopay pending only")
    assert [(f.key, f.value) for f in facts] == [("cashflow_delta:2026-Q1", "-42.5")]
    assert all(isinstance(f, CoreFact) and f.trust == "trusted" for f in facts)
    assert memory.recall(v, "unrelated words entirely") == []


def test_untrusted_approved_fact_keeps_its_label_and_is_not_a_baseline():
    v = FakeVault()
    _approve(v, "budget_baseline:2026-Q3", "310", trust="untrusted")
    facts = memory.recall(v, "budget baseline 2026 Q3")
    assert facts[0].trust == "untrusted"
    out = expense_worker(lambda: [], get_memory=lambda: facts)(
        type("S", (), {"category": None})(), {}
    )
    assert not any(f.claim_key.startswith("budget_baseline:") for f in out)
    text = render_report(RunReport([], {}, [], [], 0, 0, False, memory=facts))
    assert "From your approved memory" in text and "(untrusted-derived)" in text


def test_forget_needs_a_forget_token_bound_to_session_and_item():
    v = FakeVault()
    item = _approve(v, "goal:savings", "500")
    with pytest.raises(memory.ApprovalError):  # an approve-purpose token cannot forget
        memory.forget(
            v,
            item["id"],
            token=memory.issue_approval_token("s", item["id"]),
            session_id="s",
        )
    tok = memory.issue_approval_token("s", item["id"], "forget")
    with pytest.raises(memory.ApprovalError):
        memory.forget(v, item["id"], token=tok, session_id="other")
    with pytest.raises(memory.ApprovalError):  # consumed even on failure
        memory.forget(v, item["id"], token=tok, session_id="s")
    assert memory.forget(
        v,
        item["id"],
        token=memory.issue_approval_token("s", item["id"], "forget"),
        session_id="s",
    )
    assert memory.list_core(v, include_superseded=True) == []


def test_baseline_is_trusted_memory_only_and_never_reaches_market_inputs():
    facts = [
        CoreFact(
            key="cashflow_delta:2026-Q2",
            value="$18.25",
            approved_at="2026-11-01T00:00:00+00:00",
            trust="trusted",
        )
    ]
    out = expense_worker(lambda: [], get_memory=lambda: facts)(
        type("S", (), {"category": None})(), {}
    )
    base = [f for f in out if f.claim_key == "budget_baseline:2026-Q2"]
    assert (
        base
        and base[0].value == 18.25
        and base[0].evidence_ids == ("memory:cashflow_delta:2026-Q2",)
    )
    assert (
        dependency_inputs("expense", "market", out) == ()
    )  # only cashflow_delta:* passes downstream
    delta = Finding(
        agent="expense",
        claim_key="cashflow_delta:2026-Q2",
        value=1.0,
        summary="x",
        confidence=1,
    )
    assert dependency_inputs("expense", "market", out + [delta]) == (delta,)
    assert (
        dependency_inputs("expense", "forecaster", [delta]) == ()
    )  # undeclared edge: default deny


def test_s18_memory_has_no_bus_edge_and_no_agent_write_path():
    assert CoreFact not in EDGES.values()
    users = {
        str(p)
        for p in pathlib.Path("src").rglob("*.py")
        if "memory.forget(" in p.read_text()
    }
    assert users == {"src/interface/agents.py"}
