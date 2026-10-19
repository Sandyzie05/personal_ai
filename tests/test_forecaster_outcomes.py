from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.forecaster.models import (
    EvidenceCard, EvidenceSnapshot, HorizonPrediction, MarketData, PriceBar, ResearchPrediction,
)
from src.forecaster.outcomes import (
    ForecastRecord, SessionPrice, TradingSession, create_forecast, evaluate_outcome,
)


def close(day):
    return datetime.combine(day, datetime.min.time(), timezone.utc).replace(hour=20)


def fixture(horizon=5, adjusted=False):
    # Explicit supplied sessions cross a weekend; no calendar-day substitution.
    days = [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20),
            date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
    sessions = tuple(TradingSession(exchange="XNAS", session=day, closes_at=close(day)) for day in days)
    origin_time = close(days[0]) + timedelta(hours=1)
    evidence = EvidenceCard(source_id="yahoo-chart", source_type="market", symbol="NVDA",
                            title="Prices", summary="Frozen close", retrieved_at=origin_time, confidence=.8)
    market = MarketData(
        symbol="NVDA", exchange="XNAS", currency="USD", bars=(PriceBar(
            symbol="NVDA", session=days[0], close=100, adjusted_close=50 if adjusted else None,
            source_id="yahoo-chart", retrieved_at=origin_time),),
        price_convention="split_dividend_adjusted_close" if adjusted else "unadjusted_close",
        evidence=(evidence,))
    prediction = ResearchPrediction(
        snapshot=EvidenceSnapshot(symbol="NVDA", as_of=origin_time, created_at=origin_time,
                                  market=market, evidence=(evidence,)),
        status="completed", horizons=(HorizonPrediction(
            horizon_sessions=horizon, heuristic_score=.6, downside_score=.3, coverage=1,
            outlook="bullish", expected_excess_return_pct=2, reasoning=("test",)),),
        events=(), theme_exposures=(), source_provenance=(), limitations=())
    return create_forecast(prediction, horizon, sessions=sessions), sessions


def prices(forecast, adjusted=False):
    when = forecast.target.closes_at + timedelta(hours=1)
    return tuple(SessionPrice(
        symbol=symbol, exchange=exchange, session=session, close=value,
        adjusted_close=value / 2 if adjusted else None,
        source_id="yahoo-chart", retrieved_at=when,
    ) for symbol, exchange, session, value in (
        ("NVDA", "XNAS", forecast.origin.session, 100),
        ("NVDA", "XNAS", forecast.target.session, 110),
        ("SPY", "ARCX", forecast.origin.session, 200),
        ("SPY", "ARCX", forecast.target.session, 204),
    ))


def test_exact_session_targets_and_same_convention_benchmark():
    forecast, _ = fixture()
    assert forecast.target.session == date(2026, 10, 23)
    assert ForecastRecord.model_validate(forecast.to_api()).forecast_id == forecast.forecast_id
    result = evaluate_outcome(forecast, prices(forecast), evaluated_at=close(date(2026, 10, 26)))
    assert result.status == "evaluated"
    assert result.realized_return_pct == pytest.approx(10)
    assert result.benchmark_return_pct == pytest.approx(2)
    assert result.realized_excess_return_pct == pytest.approx(8)
    assert result.return_estimate_error_pct == pytest.approx(6)
    assert result.directional_match is True
    assert result.forecast_id == forecast.forecast_id


def test_missing_target_never_substitutes_current_quote():
    forecast, _ = fixture()
    supplied = prices(forecast)
    newer = supplied[1].model_copy(update={"session": date(2026, 10, 26),
                                          "retrieved_at": close(date(2026, 10, 26))})
    result = evaluate_outcome(forecast, (supplied[0], newer, *supplied[2:]),
                              evaluated_at=close(date(2026, 10, 26)))
    assert result.status == "missing_data"
    assert result.realized_return_pct is None


def test_immature_and_insufficient_session_sequence_are_explicit():
    forecast, _ = fixture()
    assert evaluate_outcome(forecast, (), evaluated_at=forecast.target.closes_at - timedelta(seconds=1)).status == "immature"
    long_forecast, _ = fixture(horizon=21)
    assert long_forecast.schedule_status == "session_unavailable"
    assert evaluate_outcome(long_forecast, (), evaluated_at=close(date(2026, 12, 1))).status == "session_unavailable"


def test_aware_time_duplicate_prices_and_schedule_identity_fail_closed():
    forecast, sessions = fixture()
    with pytest.raises(ValueError):
        evaluate_outcome(forecast, (), evaluated_at=datetime(2026, 11, 1))
    supplied = prices(forecast)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_outcome(forecast, (*supplied, supplied[0]), evaluated_at=close(date(2026, 11, 1)))
    with pytest.raises(ValidationError):
        ForecastRecord.model_validate(dict(forecast.to_api(), session_sequence=[s.to_api() for s in reversed(sessions)]))
    with pytest.raises(ValidationError):
        ForecastRecord.model_validate(dict(forecast.to_api(), target=sessions[1].to_api()))


def test_adjusted_convention_never_falls_back_or_changes_frozen_base():
    forecast, _ = fixture(adjusted=True)
    when = close(date(2026, 10, 26))
    assert evaluate_outcome(forecast, prices(forecast), evaluated_at=when).status == "missing_data"
    adjusted = prices(forecast, adjusted=True)
    assert evaluate_outcome(forecast, adjusted, evaluated_at=when).status == "evaluated"
    changed = adjusted[0].model_copy(update={"adjusted_close": 49})
    assert evaluate_outcome(forecast, (changed, *adjusted[1:]), evaluated_at=when).status == "missing_data"


def test_future_retrieval_cannot_supply_earlier_evaluation():
    forecast, _ = fixture()
    assert evaluate_outcome(forecast, prices(forecast),
                            evaluated_at=forecast.target.closes_at).status == "missing_data"
