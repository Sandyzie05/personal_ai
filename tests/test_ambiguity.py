"""P3: rule-based ambiguity -> one templated clarifying question; controls must not ask."""

import pytest

from evals.golden import GOLDEN
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.service import run_query
from src.agents.orchestration.workers import (
    CLARIFY,
    ambiguity,
    clarify_question,
    expense_worker,
)

CARDS = [
    {
        "storage_key": f"c{i}",
        "metadata": {
            "provider": "Contoso Bank",
            "category": "credit_card",
            "account_label": lab,
        },
        "extraction": {
            "total": 10.0 + i,
            "period_end": "2026-09-30",
            "period_start": "2026-07-01",
        },
    }
    for i, lab in enumerate(("Card 1111", "Card 2222"))
]


@pytest.mark.parametrize(
    "query,records,kind",
    [
        ("How much was my gas bill?", [], "period"),
        ("What is my total electricity bill?", [], "period"),
        ("What was my credit card bill for Q3 2026?", CARDS, "account"),
        ("Show my credit card spending this quarter.", CARDS, "account"),
        ("What should I watch in the stock market today?", [], "ticker"),
    ],
)
def test_each_ambiguity_type(query, records, kind):
    assert ambiguity(query, records) == kind


@pytest.mark.parametrize(
    "query,records",
    [
        ("How much was my gas bill in September 2026?", []),
        ("What was my credit card bill ending 1111 for Q3 2026?", CARDS),
        (
            "What was my credit card bill for Q3 2026?",
            CARDS[:1],
        ),  # one account: nothing to choose
        ("Show my gas bills and spending.", []),
        ("What should I watch on $NVDA today?", []),
        (
            "How do my utility bills affect my budget and what should I watch on AMD?",
            [],
        ),
        ("What should I watch in my portfolio?", []),
    ],
)
def test_not_ambiguous_controls(query, records):
    assert ambiguity(query, records) is None


def test_no_false_asks_on_the_golden_set():
    assert [t.id for t in GOLDEN if ambiguity(t.query, t.records)] == []


def test_questions_are_fixed_templates():
    assert clarify_question("period", "How much was my bill?", []) == CLARIFY["period"]
    q = clarify_question("account", "my credit card bill for Q3", CARDS)
    assert q.startswith(
        "Which account do you mean? You have 2 credit card accounts on file: card 1111, card 2222."
    )


def test_service_returns_one_clarify_and_runs_nothing():
    calls = []

    def worker(step, inputs):
        calls.append(step.id)
        return []

    c = Coordinator({"expense": worker})
    c.records = CARDS
    rep = run_query(c, "What was my credit card bill for Q3 2026?")
    assert (
        [e.kind for e in rep.escalations] == ["clarify"]
        and rep.steps == []
        and calls == []
    )
    c2 = Coordinator({"expense": expense_worker(lambda: CARDS)})
    c2.records = CARDS
    rep2 = run_query(c2, "What was my credit card bill for Q3 2026? Card 2222")
    assert not any(e.kind == "clarify" for e in rep2.escalations)
    assert (
        rep2.claims["bill:Contoso Bank:2026-09-30"].value == 11.0
    )  # only the named account
