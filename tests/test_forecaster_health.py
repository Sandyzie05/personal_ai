from datetime import date

from src.forecaster.health import HealthTracker
from src.forecaster.models import ProviderIssue, ProviderResult
from tests.test_forecaster_outcomes import close
from tests.test_forecaster_report import many

NOW = close(date(2026, 10, 26))


def fail(code="timeout"):
    return ProviderResult(
        status="unavailable",
        issues=(ProviderIssue(source_id="fred", code=code, message="x"),),
    )


def ok():
    return ProviderResult(status="completed", value=1)


def test_empty_tracker():
    snap = HealthTracker().snapshot()
    assert snap["status"] == "unknown" and snap["providers"] == []
    assert snap["accuracy"]["evaluated"] == 0


def test_failure_counts_and_recovery():
    t = HealthTracker()
    for _ in range(3):
        t.record("fred", fail(), latency_ms=12.345, at=NOW)
    p = t.snapshot()["providers"][0]
    assert (
        p["state"] == "down" and p["failures"] == 3 and p["consecutive_failures"] == 3
    )
    assert p["last_issue_codes"] == ["timeout"] and p["issue_counts"] == {"timeout": 3}
    assert p["last_latency_ms"] == 12.35
    t.record("fred", ok(), latency_ms=5, at=NOW)
    p = t.snapshot()["providers"][0]
    assert (
        p["state"] == "healthy"
        and p["consecutive_failures"] == 0
        and p["failures"] == 3
    )


def test_partial_is_degraded_not_failure():
    t = HealthTracker()
    partial = ProviderResult(
        status="partial",
        value=1,
        issues=(ProviderIssue(source_id="sec-edgar", code="incomplete", message="x"),),
    )
    t.record("sec-edgar", partial, at=NOW)
    p = t.snapshot()["providers"][0]
    assert p["state"] == "degraded" and p["partials"] == 1 and p["failures"] == 0
    assert p["last_latency_ms"] is None


def test_snapshot_overall_and_accuracy():
    t = HealthTracker()
    t.record("fred", ok(), at=NOW)
    t.record("sec-edgar", fail("http"), at=NOW)
    snap = t.snapshot(many(3))
    assert snap["status"] == "degraded"
    assert [p["source_id"] for p in snap["providers"]] == ["fred", "sec-edgar"]
    assert snap["accuracy"]["evaluated"] == 3
    assert snap["accuracy"]["directional_accuracy_pct"] is None
