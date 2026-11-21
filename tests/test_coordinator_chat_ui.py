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
