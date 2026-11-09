import math
from datetime import date, datetime, timedelta, timezone

import pytest

from src.forecaster.models import EvidenceCard, MarketData, PriceBar
from src.forecaster.technical import ema, macd_history, rsi, technical_snapshot

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def bars(closes, symbol="NVDA"):
    start = date(2025, 1, 1)
    return tuple(
        PriceBar(
            symbol=symbol,
            session=start + timedelta(days=i),
            close=c,
            source_id="yahoo-chart",
            retrieved_at=NOW,
        )
        for i, c in enumerate(closes)
    )


def market(closes):
    card = EvidenceCard(
        source_id="yahoo-chart",
        source_type="market",
        symbol="NVDA",
        title="Prices",
        summary="closes",
        retrieved_at=NOW,
        confidence=0.8,
    )
    return MarketData(
        symbol="NVDA",
        exchange="XNAS",
        currency="USD",
        bars=bars(closes),
        price_convention="unadjusted_close",
        evidence=(card,),
    )


def test_too_few_bars_is_explicit():
    s = technical_snapshot(bars([10, 11, 12]))
    assert s.status == "insufficient_data" and s.trend == "insufficient_data"
    assert s.rsi_14 is None and s.sma_20 is None and s.support is None
    assert any(u.startswith("sma_20") for u in s.unavailable)


def test_empty_bars():
    s = technical_snapshot((), symbol="NVDA")
    assert s.status == "insufficient_data" and s.as_of_session is None


def test_atr_and_volume_never_fabricated():
    s = technical_snapshot(market([100 + i for i in range(60)]))
    assert s.atr is None and s.volume is None
    assert any(u.startswith("atr") for u in s.unavailable)
    assert any(u.startswith("volume") for u in s.unavailable)


def test_uptrend_indicators():
    s = technical_snapshot(market([100 + i for i in range(60)]))
    assert s.status == "partial" and s.trend == "bullish"
    assert s.rsi_14 == 100.0
    assert s.macd > 0 and s.macd_signal is not None
    assert s.sma_20 > s.sma_50 and s.sma_200 is None
    assert s.support == 110 and s.resistance == 159
    assert s.bollinger_upper > s.sma_20 > s.bollinger_lower
    assert len(s.macd_history) == 10


def test_downtrend():
    s = technical_snapshot(market([200 - i for i in range(60)]))
    assert s.trend == "bearish" and s.rsi_14 == 0.0 and s.macd < 0


def test_flat_prices():
    s = technical_snapshot(market([50.0] * 60))
    assert s.trend == "sideways"
    assert s.rsi_14 == 50.0
    assert s.annualized_volatility_pct == 0
    assert s.macd == 0 and s.macd_histogram == 0
    assert s.bollinger_upper == s.bollinger_lower == 50


def test_known_values():
    assert ema([1, 2, 3], 3) == pytest.approx(2.25)
    # last 4 deltas: +1 -1 +1 -1 -> gain 0.5, loss 0.5
    assert rsi([1, 2, 1, 2, 1, 2], 4) == pytest.approx(50.0)
    assert len(macd_history([float(i) for i in range(40)])) == 14


def test_volatility_annualised_percent():
    s = technical_snapshot(bars([100, 101] * 15))
    assert s.annualized_volatility_pct == pytest.approx(
        math.sqrt(252) * 100 * 0.0099, rel=0.05
    )


def test_adjusted_close_used_when_all_bars_adjusted():
    start = date(2025, 1, 1)
    b = tuple(
        PriceBar(
            symbol="NVDA",
            session=start + timedelta(days=i),
            close=100,
            adjusted_close=50,
            source_id="yahoo-chart",
            retrieved_at=NOW,
        )
        for i in range(5)
    )
    s = technical_snapshot(b)
    assert s.price_field == "adjusted_close" and s.last_price == 50


def test_atr_and_volume_from_bars_else_unavailable():
    from datetime import date, datetime, timedelta, timezone
    from src.forecaster.models import PriceBar
    from src.forecaster.technical import technical_snapshot

    def bars(with_hlv):
        return tuple(PriceBar(symbol="AAPL", session=date(2026, 1, 1) + timedelta(days=i), close=100 + i,
                              high=102 + i if with_hlv else None, low=98 + i if with_hlv else None,
                              volume=1000 + i if with_hlv else None, source_id="yahoo-chart",
                              retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc)) for i in range(40))

    t = technical_snapshot(bars(True))
    assert t.atr == 4.0 and t.volume == 1039 and t.volume_ratio > 1
    t = technical_snapshot(bars(False))
    assert t.atr is None and t.volume is None and any(u.startswith("atr") for u in t.unavailable)


def test_news_failures_use_news_source_id():
    import asyncio
    from src.forecaster.providers import PublicDataProviders
    r = asyncio.run(PublicDataProviders().news("me@example.com"))
    assert r.issues[0].source_id == "yahoo-news"
