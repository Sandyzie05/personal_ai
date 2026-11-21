"""P2: the verifier checks that a claim cites the record it names, not just that the number appears somewhere."""

from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.schemas import Finding, PlanStep
from src.agents.orchestration.verifier import records_evidence, verify_claims
from src.agents.orchestration.workers import expense_worker, named_account, plan_for


def rec(key, total, end, prov="Northwind Gas", cat="gas", acct=None):
    meta = {
        "provider": prov,
        "category": cat,
        **({"account_label": acct} if acct else {}),
    }
    start = {"06": "2026-04-01", "09": "2026-07-01"}[end[5:7]]
    return {
        "storage_key": key,
        "metadata": meta,
        "extraction": {"total": total, "period_end": end, "period_start": start},
    }


RECS = [
    rec("a", 50.0, "2026-06-30"),
    rec("b", 60.0, "2026-09-30"),
    rec("c", 60.0, "2026-09-30", prov="Contoso Power", cat="electricity"),
]
EV = records_evidence(RECS)


def claim(key, value, ev):
    return Finding(
        agent="expense",
        claim_key=key,
        value=value,
        summary="s",
        evidence_ids=ev,
        confidence=0.9,
    )


def kinds(claims, **kw):
    return [(e.kind, e.claim_key) for e in verify_claims(claims, EV, **kw)]


def test_right_record_passes():
    assert (
        kinds(
            {
                "bill:Northwind Gas:2026-09-30": claim(
                    "bill:Northwind Gas:2026-09-30", 60.0, ("b",)
                )
            }
        )
        == []
    )


def test_right_figure_wrong_period_is_unsupported():
    k = "bill:Northwind Gas:2026-06-30"
    out = verify_claims(
        {k: claim(k, 60.0, ("b",))}, EV
    )  # 60 is in record b, but b is the Q3 bill
    assert [(e.kind, e.claim_key) for e in out] == [
        ("unsupported", k)
    ] and "Northwind Gas/2026-09-30" in out[0].detail


def test_right_figure_wrong_provider_is_unsupported():
    k = "bill:Northwind Gas:2026-09-30"
    assert kinds({k: claim(k, 60.0, ("c",))}) == [("unsupported", k)]


def test_wrong_category_and_wrong_account_are_unsupported():
    k = "bill:Northwind Gas:2026-09-30"
    assert kinds({k: claim(k, 60.0, ("b",))}, category="electricity") == [
        ("unsupported", k)
    ]
    cards = [
        rec("x", 10.0, "2026-09-30", "Contoso Bank", "credit_card", "Card 1111"),
        rec("y", 10.0, "2026-09-30", "Contoso Bank", "credit_card", "Card 2222"),
    ]
    k2 = "bill:Contoso Bank:2026-09-30"
    out = verify_claims(
        {k2: claim(k2, 10.0, ("y",))}, records_evidence(cards), account="card 1111"
    )
    assert [e.kind for e in out] == ["unsupported"] and "account" in out[0].detail
    assert (
        verify_claims(
            {k2: claim(k2, 10.0, ("x",))}, records_evidence(cards), account="card 1111"
        )
        == []
    )


def test_aggregate_must_cite_records_inside_its_quarters():
    k = "cashflow_delta:2026-Q3"
    assert (
        kinds({k: claim(k, 50.0, ("a", "b"))}) == []
    )  # Q2 + Q3 records for a Q3 delta
    old = rec("z", 10.0, "2026-06-30")
    old["extraction"]["period_end"] = (
        "2025-12-31"  # a Q4 2025 record cited for a Q3 2026 delta
    )
    ev = records_evidence(RECS + [old])
    out = verify_claims({k: claim(k, 10.0, ("z",))}, ev)
    assert [e.kind for e in out] == ["unsupported"] and "outside 2026-Q3" in out[
        0
    ].detail


def test_named_account_and_expense_filter():
    cards = [
        rec("x1", 10.0, "2026-06-30", "Contoso Bank", "credit_card", "Card 1111"),
        rec("x2", 40.0, "2026-09-30", "Contoso Bank", "credit_card", "Card 1111"),
        rec("y1", 99.0, "2026-09-30", "Contoso Bank", "credit_card", "Card 2222"),
    ]
    assert (
        named_account("my card ending 1111 for Q3", cards, "credit_card") == "card 1111"
    )
    assert named_account("my credit card bill", cards, "credit_card") is None
    step = PlanStep(
        id="expense",
        agent="expense",
        goal="g",
        category="credit_card",
        account="card 1111",
    )
    out = expense_worker(lambda: cards)(step, {})
    assert {f.claim_key: f.value for f in out if f.claim_key.startswith("bill:")} == {
        "bill:Contoso Bank:2026-06-30": 10.0,
        "bill:Contoso Bank:2026-09-30": 40.0,
    }


def test_corrected_bill_carries_single_source_caveat():
    old = {
        **rec("old", 99.0, "2026-09-30"),
        "metadata": {
            "provider": "Northwind Gas",
            "category": "gas",
            "superseded": True,
        },
    }
    r = Coordinator({"expense": expense_worker(lambda: RECS[:2] + [old])}).run(
        plan_for("my gas bills")
    )
    assert "single_source:bill:Northwind Gas:2026-09-30" in r.claims
    assert any(e.kind == "single_source" for e in r.escalations)
    assert (
        r.claims["bill:Northwind Gas:2026-09-30"].value == 60.0
    )  # the replacement, never the superseded copy
