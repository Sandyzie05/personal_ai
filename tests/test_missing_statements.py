"""P12: an overdue ledger entry becomes a reported missing statement; no total is invented around it."""

from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.workers import expense_worker, plan_for


def rec(key, prov, total, end, cat="electricity"):
    start = {"06": "2026-04-01", "09": "2026-07-01"}[end[5:7]]
    return {
        "storage_key": key,
        "metadata": {"provider": prov, "category": cat},
        "extraction": {"total": total, "period_end": end, "period_start": start},
    }


RECS = [
    rec("a1", "Contoso Power", 100.0, "2026-06-30"),
    rec("a2", "Contoso Power", 120.0, "2026-09-30"),
    rec("b1", "Fabrikam Water", 40.0, "2026-06-30", "electricity"),
]
LATE = [
    {
        "provider": "Fabrikam Water",
        "last_period_end": "2026-06-30",
        "days_overdue": 12,
        "cadence": "quarterly",
    }
]


def run(overdue, query="Show my electricity bills and spending.", records=RECS):
    return Coordinator(
        {"expense": expense_worker(lambda: records, lambda: overdue)}
    ).run(plan_for(query))


def test_overdue_becomes_missing_finding_and_delta_is_not_confident():
    r = run(LATE)
    m = r.claims["missing:Fabrikam Water:2026-09-30"]
    assert m.status == "unavailable" and m.value is None
    d = r.claims["cashflow_delta:2026-Q3"]
    assert d.status == "uncertain" and "missing statement Fabrikam Water" in d.summary
    assert [e.kind for e in r.escalations if e.claim_key.startswith("missing:")] == [
        "missing_statement"
    ]
    assert "bill:Fabrikam Water:2026-09-30" not in r.claims  # never invented


def test_no_ledger_entry_keeps_previous_behaviour():
    r = run([])
    assert not any(k.startswith("missing:") for k in r.claims)
    assert r.claims["cashflow_delta:2026-Q3"].status == "ok"


def test_other_category_provider_is_not_reported_and_monthly_cadence():
    gas = [rec("g1", "Northwind Gas", 30.0, "2026-06-30", "gas")]
    r = run(
        [
            {
                "provider": "Northwind Gas",
                "last_period_end": "2026-06-30",
                "days_overdue": 9,
                "cadence": "monthly",
            }
        ],
        records=RECS + gas,
    )
    assert not any(
        k.startswith("missing:") for k in r.claims
    )  # asked about electricity; the gas bill is not this
    r2 = run(
        [
            {
                "provider": "Northwind Gas",
                "last_period_end": "2026-08-31",
                "days_overdue": 9,
                "cadence": "monthly",
            }
        ],
        query="Show my bills and spending.",
        records=RECS + gas,
    )
    assert "missing:Northwind Gas:2026-09-30" in r2.claims


def test_missing_reported_even_with_no_records():
    r = run(LATE, records=[])
    assert (
        r.claims["records"].status == "unavailable"
        and "missing:Fabrikam Water:2026-09-30" in r.claims
    )
