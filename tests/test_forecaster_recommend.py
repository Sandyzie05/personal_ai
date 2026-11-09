from datetime import datetime, timezone

import pytest

from src.forecaster.models import (
    EvidenceCard,
    EvidenceSnapshot,
    HorizonPrediction,
    MarketEvent,
    ResearchPrediction,
)
from src.forecaster.recommend import (
    confidence,
    rank_leans,
    research_lean,
    select_diversified,
    sentiment_score,
)
from src.forecaster.technical import TechnicalSnapshot, technical_snapshot
from tests.test_forecaster_technical import bars

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def tech(**kw):
    base = dict(
        symbol="NVDA",
        as_of_session=None,
        bar_count=250,
        status="completed",
        price_field="close",
        trend="bullish",
        rsi_14=55.0,
        macd=1.0,
        macd_signal=0.5,
        sma_20=120.0,
        sma_50=110.0,
        sma_200=100.0,
        annualized_volatility_pct=18.0,
    )
    base.update(kw)
    return TechnicalSnapshot(**base)


def prediction(score=0.8, coverage=1.0, events=()):
    card = EvidenceCard(
        source_id="yahoo-chart",
        source_type="market",
        symbol="NVDA",
        title="P",
        summary="s",
        retrieved_at=NOW,
        confidence=0.8,
    )
    snap = EvidenceSnapshot(symbol="NVDA", as_of=NOW, created_at=NOW, evidence=(card,))
    h = HorizonPrediction(
        horizon_sessions=5,
        heuristic_score=score,
        downside_score=0.2,
        coverage=coverage,
        outlook="bullish",
        expected_excess_return_pct=1,
        reasoning=(),
    )
    return ResearchPrediction(
        snapshot=snap,
        status="completed",
        horizons=(h,),
        events=events,
        theme_exposures=(),
        source_provenance=(),
        limitations=(),
    )


def test_no_technical_is_insufficient():
    r = research_lean(None)
    assert (
        r.status == "insufficient_evidence" and r.lean is None and r.risk_level is None
    )
    assert "not financial advice" in " ".join(r.limitations)


def test_too_few_bars_is_insufficient():
    r = research_lean(technical_snapshot(bars([10, 11, 12])), prediction())
    assert r.status == "insufficient_evidence"


def test_bullish_with_sentiment():
    r = research_lean(tech(), prediction(0.9))
    assert r.status == "completed" and r.lean == "bullish"
    assert 0 < r.confidence <= 0.95 and r.risk_level in {"low", "moderate"}
    assert r.technical_score == pytest.approx((80 + 75 + 85 + 70 + 75) / 5)


def test_bearish():
    t = tech(
        trend="bearish",
        rsi_14=80.0,
        macd=-1.0,
        macd_signal=0.0,
        sma_20=90.0,
        sma_50=100.0,
        sma_200=110.0,
        annualized_volatility_pct=60.0,
    )
    r = research_lean(t, prediction(0.1))
    assert r.lean == "bearish" and r.risk_level in {"high", "very_high"}


def test_missing_sentiment_caps_confidence_and_says_so():
    r = research_lean(tech())
    assert r.status == "completed" and r.sentiment_score is None
    assert r.confidence <= 0.5
    assert any("missing" in x for x in r.reasoning)


def test_abstained_prediction_is_not_used():
    p = prediction().model_copy(update={"status": "abstained"})
    assert sentiment_score(p) is None


def test_no_trade_language_in_output():
    dump = research_lean(tech(), prediction(0.9)).model_dump_json().lower()
    for word in ("buy", "sell", "target_price", "stop_loss"):
        assert word not in dump


def test_events_drive_sentiment():
    ev = MarketEvent(
        event_type="x",
        direction="demand_down",
        magnitude=1.0,
        evidence_id="e",
        source_id="yahoo-chart",
    )
    assert sentiment_score(None, (ev,)) == 0


def test_low_coverage_blends_to_neutral():
    assert sentiment_score(prediction(1.0, coverage=0.0)) is None
    assert sentiment_score(prediction(1.0, coverage=0.5)) == pytest.approx(75)


def test_confidence_agreement():
    assert confidence([80, 80]) > confidence([80, 20])
    assert 0.1 <= confidence([50, 50]) <= 0.95


def test_rank_and_diversify():
    base = research_lean(tech(), prediction(0.9))
    a = base.model_copy(update={"symbol": "AAA"})
    c = base.model_copy(update={"symbol": "CCC"})
    b = research_lean(tech(trend="sideways"), prediction(0.5)).model_copy(
        update={"symbol": "BBB"}
    )
    ranked = rank_leans([b, research_lean(None), c, a])
    assert [r.lean.symbol for r in ranked] == ["AAA", "CCC", "BBB"]
    assert [r.rank for r in ranked] == [1, 2, 3]
    picked = select_diversified(
        ranked, max_per_theme=1, themes={"AAA": "t", "CCC": "t", "BBB": "u"}
    )
    assert [r.lean.symbol for r in picked] == ["AAA", "BBB"]
