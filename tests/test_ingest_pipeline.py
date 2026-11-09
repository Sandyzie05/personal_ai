from datetime import date

from src.data_ingestion.pipeline import (Candidate, ingest_candidate, IngestReport, overdue, run_ingest,
                                          set_expectation, sync_status)
from tests.test_ingest_files import make_pdf
from tests.test_orchestration import FakeVault


def cand(text="Total due: $84.20", **kw):
    kw = {"source": "manual", "provider": "Mock Power", "category": "electricity", "period_end": "2026-09-30", **kw}
    return Candidate(make_pdf(text), **kw)


def test_ingest_is_idempotent_and_indexes_once():
    v, seen = FakeVault(), []
    r, c = IngestReport(), cand()  # same bytes = same re-download
    assert ingest_candidate(v, c, lambda k, d: seen.append(k), report=r) == "new"
    assert ingest_candidate(v, c, lambda k, d: seen.append(k), report=r) == "duplicate"
    assert len(seen) == 1 and r.duplicates == 1
    key = seen[0]
    assert v.d[key]["metadata"]["original_filename"].endswith(".pdf") and "84.20" in v.d[key]["text_content"]


def test_corrected_bill_supersedes_old():
    v, gone = FakeVault(), []
    ingest_candidate(v, cand("Total due: $84.20"))
    r = IngestReport()
    ingest_candidate(v, cand("Total due: $79.00 corrected"), on_superseded=gone.append, report=r)
    assert len(gone) == 1 and r.superseded
    assert sorted(d["status"] for d in v.d["ingest_manifest"]["docs"].values()) == ["indexed", "superseded"]


def test_hostile_file_rejected_not_stored():
    v = FakeVault()
    r = IngestReport()
    c = cand()
    c.data += b"\n% /JavaScript"
    assert ingest_candidate(v, c, report=r) == "failed" and not v.d and "rejected" in r.failed[0]


def test_failed_verify_never_half_indexed():
    v, seen = FakeVault(), []
    r = IngestReport()
    assert ingest_candidate(v, cand(), seen.append, verify=lambda k, d: False, report=r) == "failed"
    assert not seen and list(v.d["ingest_manifest"]["docs"].values())[0]["status"] == "failed"


def test_run_caps_and_fetcher_isolation():
    v = FakeVault()

    def boom():
        raise RuntimeError("secret detail")

    def many():
        return [cand(f"Total due: ${i}.00 x", period_end=f"2026-0{1 + i % 9}-28") for i in range(5)]

    r = run_ingest(v, [boom, many], max_files=3)
    assert len(r.new) == 3 and any("fetcher" in f for f in r.failed) and "secret" not in r.summary()
    assert "run cap" in r.needs_you[0]


def test_expected_bills_ledger():
    v = FakeVault()
    set_expectation(v, "Mock Power", "monthly")
    set_expectation(v, "Mock Gas", "monthly")
    ingest_candidate(v, cand(period_end="2026-08-31"))
    o = {x["provider"]: x for x in overdue(v, date(2026, 10, 25))}
    assert o["Mock Power"]["days_overdue"] == 55 - 31 and o["Mock Gas"]["last_period_end"] is None
    assert sync_status(v)["counts"] == {"indexed": 1}


def test_ledger_manifest_recipes_credentials_are_internal_keys():
    from src.data_vault.vault import is_internal_vault_key as internal

    assert all(internal(k) for k in ("ingest_manifest", "ingest_expectations", "portal_recipes", "portal_cred_mock_power"))
    assert not internal("ingest_0123456789abcdef")  # the documents themselves are normal vault docs
