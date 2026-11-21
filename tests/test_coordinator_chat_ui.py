"""P3/P6/P12 chat-side behaviour of the Coordinator: clarify reply, follow-up buttons (AppTest, no network, no model).

Throwaway HOME + registered DEK (AGENTS.md "Testing the UI without a browser"); the coordinator is replaced by one built
from synthetic records, so nothing reads the real vault or calls a model."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.agents.orchestration import service
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.verifier import records_evidence
from src.agents.orchestration.workers import expense_worker
from src.security.auth import AuthManager

MAIN = str(Path(__file__).parent.parent / "src" / "interface" / "main.py")


def _recs():
    return [
        {
            "storage_key": f"k{i}",
            "metadata": {"provider": "Northwind Gas", "category": "gas"},
            "extraction": {"total": t, "period_end": e, "period_start": s},
        }
        for i, (t, e, s) in enumerate(
            [(30.0, "2026-06-30", "2026-04-01"), (45.0, "2026-09-30", "2026-07-01")]
        )
    ]


@pytest.fixture
def chat(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    dek = AuthManager().register("coordinator-ui-test")
    overdue = [
        {
            "provider": "Contoso Power",
            "last_period_end": "2026-06-30",
            "days_overdue": 20,
            "cadence": "quarterly",
        }
    ]

    def build(vault, get_records, *a, **kw):
        recs = _recs()
        c = Coordinator({"expense": expense_worker(lambda: recs, lambda: overdue)})
        c.evidence, c.records = records_evidence(recs), recs
        return c

    monkeypatch.setattr(service, "build_coordinator", build)
    at = AppTest.from_file(MAIN, default_timeout=120)
    at.session_state["vault_key"] = dek
    at.session_state["active_agent_id"] = "builtin-coordinator"
    at.run()
    assert not at.exception
    return at


def _send(at, text):
    at.chat_input[0].set_value(text).run()
    assert not at.exception


def _said(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_ambiguous_question_gets_one_clarifying_question_then_replans(chat):
    _send(chat, "How much was my bill?")
    assert "Which period do you mean?" in _said(chat)
    assert chat.session_state["coord_clarify"]["query"] == "How much was my bill?"
    _send(chat, "Q3 2026")
    assert (
        "coord_clarify" not in chat.session_state
    )  # answered: the combined question was planned
    assert "bill:Northwind Gas:2026-09-30" in _said(chat).replace("\\", "")


def _vault(at):
    from src.data_vault import DataVault

    return DataVault(
        vault_path=str(Path.home() / ".personal_ai_vault"),
        encryption_key=at.session_state["vault_key"],
    )


def _log(at):
    from src.agents import store

    return [e for e in store.list_activity(_vault(at), 50) if e.get("kind") == "review"]


def test_missing_statement_offers_sync_now_and_never_fetches(chat):
    _send(chat, "Show my gas bills and spending.")
    flags = chat.session_state["coord_last"]["escalations"]
    assert {
        "kind": "missing_statement",
        "claim_key": "missing:Contoso Power:2026-09-30",
    } in flags
    sync = [b for b in chat.button if b.label == "Sync now"]
    assert sync, "a missing statement must offer Sync now"
    sync[0].click().run()
    assert (
        not chat.exception and chat.session_state["nav_page"] == "upload"
    )  # opens the Sync panel, fetches nothing


def test_review_buttons_log_decisions_without_values(chat):
    _send(chat, "Show my gas bills and spending.")
    next(b for b in chat.button if b.label == "Accept").click().run()
    next(b for b in chat.button if b.label == "Dismiss").click().run()
    assert not chat.exception
    entries = _log(chat)
    assert {e["decision"] for e in entries} == {"accept", "dismiss"}
    assert all(
        set(e) <= {"ts", "kind", "run_id", "escalation_kind", "claim_key", "decision"}
        for e in entries
    )
    assert not any(
        "45.0" in str(list(e.values())) or "30.0" in str(list(e.values()))
        for e in entries
    )


def test_correct_becomes_a_core_memory_proposal_not_a_commit(chat):
    from src.agents.orchestration import memory

    _send(chat, "Show my gas bills and spending.")
    next(t for t in chat.toggle if t.label == "Correct").set_value(True).run()
    next(t for t in chat.text_input if t.label == "Correct value").set_value(
        "41.50"
    ).run()
    next(b for b in chat.button if b.label == "Propose correction").click().run()
    assert not chat.exception
    assert memory.list_core(_vault(chat)) == []
    assert any(
        p["key"].startswith("correction:") and p["value"] == "41.50"
        for p in memory.list_pending(_vault(chat))
    )
    assert [e["decision"] for e in _log(chat)] == ["correct"]
