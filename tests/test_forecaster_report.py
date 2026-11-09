import csv
import io
from datetime import date, timedelta

from src.forecaster.outcomes import evaluate_outcome
from src.forecaster.report import (
    MIN_SAMPLE,
    csv_safe,
    export_rows,
    report,
    scorable,
    summarize,
    to_csv,
    trend_summary,
)
from tests.test_forecaster_outcomes import close, fixture, prices

EVAL = close(date(2026, 10, 26))


def pair(excess=1.0, expected=2.0, shift=0, notes=None):
    """Origin 100, SPY +2%: target close = 102 + excess gives that realized excess."""
    forecast, _ = fixture()
    forecast = forecast.model_copy(
        update={
            "expected_excess_return_pct": expected,
            "as_of": forecast.as_of + timedelta(minutes=shift),
        }
    )
    target = 102 + excess
    px = tuple(
        p.model_copy(update={"close": target})
        if (p.symbol, p.session) == ("NVDA", forecast.target.session)
        else p
        for p in prices(forecast)
    )
    outcome = evaluate_outcome(forecast, px, evaluated_at=EVAL)
    assert outcome.status == "evaluated"
    if notes:
        outcome = outcome.model_copy(update={"notes": notes})
    return forecast, outcome


def many(n, excess=1.0, start=0):
    return [pair(excess=excess, shift=start + i) for i in range(n)]


def test_empty_history():
    s = summarize([])
    assert s["evaluated"] == 0 and s["directional_accuracy_pct"] is None
    assert "insufficient sample" in s["directional_accuracy_label"]
    r = report([])
    assert r["trend"]["status"] == "insufficient-history" and r["daily"] == []
    assert export_rows([]) == []
    assert to_csv([]).startswith("forecast_id,")


def test_tiny_sample_no_percentage_claim():
    r = report(many(3))
    s = r["summary"]
    assert s["evaluated"] == 3
    assert (
        s["directional_accuracy_pct"] is None and s["mean_abs_return_error_pct"] is None
    )
    assert "insufficient sample" in s["directional_accuracy_label"]
    assert "%" not in r["narrative"][0].split("insufficient")[0]
    assert r["daily"][0]["directional_accuracy_pct"] is None


def test_full_sample_accuracy_and_baselines():
    pairs = many(MIN_SAMPLE)
    s = summarize(pairs)
    assert s["directional_accuracy_pct"] == 100.0 and s["coverage_pct"] == 100.0
    assert s["baselines"]["always_up"]["accuracy_pct"] == 100.0
    assert s["mean_abs_return_error_pct"] == 1.0  # |1 - 2|
    assert any("always-up" in line for line in report(pairs)["narrative"])


def test_wrong_direction_and_abstention():
    pairs = many(MIN_SAMPLE, excess=-1.0) + [pair(expected=0, shift=99)]
    s = summarize(pairs)
    assert s["directional_accuracy_pct"] == 0.0
    assert s["abstained"] == 1 and s["directional_calls"] == MIN_SAMPLE
    assert s["baselines"]["always_up"]["accuracy_pct"] == 9.09  # 1 of 11 went up


def test_unelapsed_horizon_excluded():
    forecast, _ = fixture()
    immature = evaluate_outcome(
        forecast, (), evaluated_at=forecast.as_of + timedelta(hours=2)
    )
    assert immature.status == "immature"
    s = summarize([(forecast, immature), (forecast, None)])
    assert s["evaluated"] == 0 and s["pending"] == 2 and s["unresolved"] == 0
    f, o = pair()
    early = o.model_copy(
        update={"evaluated_at": f.target.closes_at - timedelta(hours=1)}
    )
    assert scorable([(f, early)]) == []
    assert export_rows([(f, early)])[0]["realized_return_pct"] == ""


def test_mismatched_outcome_and_late_evaluation_excluded():
    f, o = pair()
    assert scorable([(f, o.model_copy(update={"forecast_id": "x"}))]) == []
    assert scorable([(f, o)], as_of=EVAL - timedelta(days=1)) == []
    assert len(scorable([(f, o)], as_of=EVAL)) == 1


def test_persistence_baseline_has_no_lookahead():
    # All forecasts share the same cutoff window, so no prior target has closed yet.
    assert summarize(many(MIN_SAMPLE))["baselines"]["persistence"]["n"] == 0


def test_trend_states():
    assert trend_summary(many(4))["status"] == "insufficient-history"
    prior = many(7, excess=-1.0)
    recent = many(7, excess=1.0, start=100)
    assert trend_summary(prior + recent)["status"] == "improving"
    assert (
        trend_summary(recent + many(7, excess=-1.0, start=200))["status"] == "slipping"
    )


def test_csv_formula_injection_neutralized():
    for bad in ("=1+1", "+cmd", "-2", "@SUM(A1)"):
        assert csv_safe(bad) == "'" + bad
    assert csv_safe("ok") == "ok" and csv_safe(-1.5) == -1.5
    f, o = pair(notes=('=HYPERLINK("http://evil")', "@x"))
    rows = list(csv.DictReader(io.StringIO(to_csv([(f, o)]))))
    assert rows[0]["notes"].startswith("'=HYPERLINK") and "| @x" in rows[0]["notes"]
    assert rows[0]["status"] == "evaluated" and rows[0]["directional_match"] == "true"


def test_export_pending_row_has_no_metrics():
    f, _ = fixture()
    row = export_rows([(f, None)])[0]
    assert row["status"] == "pending" and row["realized_return_pct"] == ""
