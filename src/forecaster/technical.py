"""Deterministic close-only technical snapshot (ported from stock_predictor stock_analyzer).

PriceBar carries no high/low/volume, so ATR and volume analysis are reported as
explicitly unavailable instead of being approximated.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

from .models import Contract, MarketData, PriceBar

MIN_BARS = 20  # trend/volatility/bands need this much history
Trend = Literal["bullish", "bearish", "sideways", "insufficient_data"]


class TechnicalSnapshot(Contract):
    symbol: str
    as_of_session: date | None
    bar_count: int
    status: Literal["completed", "partial", "insufficient_data"]
    price_field: Literal["close", "adjusted_close"]
    last_price: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_history: tuple[float, ...] = ()
    bollinger_upper: float | None = None
    bollinger_lower: float | None = None
    trend: Trend = "insufficient_data"
    # Close-based levels (no intraday high/low available).
    support: float | None = None
    resistance: float | None = None
    annualized_volatility_pct: float | None = None
    atr: float | None = None  # 14-bar average true range; needs high/low on the last 15 bars
    volume: int | None = None  # last session's volume
    avg_volume_20: float | None = None
    volume_ratio: float | None = None  # last volume / 20-bar average
    unavailable: tuple[str, ...] = ()


def ema(prices: list[float], period: int) -> float:
    k = 2 / (period + 1)
    value = prices[0]
    for price in prices[1:]:
        value = price * k + value * (1 - k)
    return value


def rsi(prices: list[float], period: int = 14) -> float:
    deltas = [b - a for a, b in zip(prices, prices[1:])][-period:]
    gain = sum(d for d in deltas if d > 0) / period
    loss = sum(-d for d in deltas if d < 0) / period
    if loss == 0:
        return 50.0 if gain == 0 else 100.0  # flat series is neutral, not overbought
    return 100 - 100 / (1 + gain / loss)


def macd_history(prices: list[float]) -> list[float]:
    # Incremental EMAs equal the original per-prefix recomputation, in O(n).
    k12, k26 = 2 / 13, 2 / 27
    e12 = e26 = prices[0]
    out = []
    for i, p in enumerate(prices[1:], start=1):
        e12 = p * k12 + e12 * (1 - k12)
        e26 = p * k26 + e26 * (1 - k26)
        if i >= 26:
            out.append(e12 - e26)
    return out


def classify_trend(prices: list[float]) -> Trend:
    if len(prices) < MIN_BARS:
        return "insufficient_data"
    recent = prices[-20:]
    short, long = sum(recent[-5:]) / 5, sum(recent) / 20
    if short > long * 1.02:
        return "bullish"
    if short < long * 0.98:
        return "bearish"
    return "sideways"


def annualized_volatility_pct(prices: list[float]) -> float | None:
    if len(prices) < MIN_BARS:
        return None
    window = prices[-30:]
    returns = [b / a - 1 for a, b in zip(window, window[1:])]
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 4)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def technical_snapshot(
    market: MarketData | tuple[PriceBar, ...], symbol: str | None = None
) -> TechnicalSnapshot:
    if isinstance(market, MarketData):
        bars, symbol = market.bars, market.symbol
        adjusted = market.price_convention == "split_dividend_adjusted_close"
    else:
        bars = tuple(market)
        symbol = symbol or (bars[0].symbol if bars else "")
        adjusted = bool(bars) and all(b.adjusted_close is not None for b in bars)
    field = "adjusted_close" if adjusted else "close"
    prices = [float(getattr(b, field)) for b in bars]
    n = len(prices)
    missing: list[str] = []
    base = dict(symbol=symbol or "UNKNOWN", bar_count=n, price_field=field)
    if n == 0:
        return TechnicalSnapshot(
            as_of_session=None,
            status="insufficient_data",
            unavailable=("prices: no bars", *missing),
            **base,
        )
    v: dict = dict(last_price=prices[-1])
    if n >= 20:
        mid = _mean(prices[-20:])
        sd = math.sqrt(sum((p - mid) ** 2 for p in prices[-20:]) / 20)
        v.update(sma_20=mid, bollinger_upper=mid + 2 * sd, bollinger_lower=mid - 2 * sd)
    if n >= 50:
        v["sma_50"] = _mean(prices[-50:])
    if n >= 200:
        v["sma_200"] = _mean(prices[-200:])
    if n >= 12:
        v["ema_12"] = ema(prices, 12)
    if n >= 26:
        v["ema_26"] = ema(prices, 26)
        v["macd"] = v["ema_12"] - v["ema_26"]
    if n >= 15:
        v["rsi_14"] = rsi(prices)
    if n >= 35:
        hist = macd_history(prices)
        if len(hist) >= 9:
            sig = ema(hist, 9)
            v.update(
                macd_signal=sig,
                macd_histogram=v["macd"] - sig,
                macd_history=tuple(hist[-10:]),
            )
    if n >= MIN_BARS:
        recent = prices[-50:]
        v.update(
            support=min(recent),
            resistance=max(recent),
            annualized_volatility_pct=annualized_volatility_pct(prices),
        )
    tail = bars[-15:]
    if len(tail) == 15 and all(b.high and b.low for b in tail):
        trs = [max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)) for a, b in zip(tail, tail[1:])]
        v["atr"] = _mean(trs)
    else:
        missing.append("atr: needs high/low on the last 15 bars")
    vols = [b.volume for b in bars[-20:]]
    if len(vols) == 20 and all(x is not None for x in vols):
        avg = _mean(vols)
        v.update(volume=vols[-1], avg_volume_20=avg, volume_ratio=round(vols[-1] / avg, 4) if avg else None)
    else:
        missing.append("volume: needs volume on the last 20 bars")
    for key, need in (
        ("sma_20", 20),
        ("sma_50", 50),
        ("sma_200", 200),
        ("rsi_14", 15),
        ("macd", 26),
        ("macd_signal", 35),
    ):
        if key not in v:
            missing.append(f"{key}: needs {need} bars, have {n}")
    status = (
        "completed" if n >= 200 else "partial" if n >= MIN_BARS else "insufficient_data"
    )
    return TechnicalSnapshot(
        as_of_session=bars[-1].session,
        status=status,
        trend=classify_trend(prices),
        unavailable=tuple(missing),
        **v,
        **base,
    )
