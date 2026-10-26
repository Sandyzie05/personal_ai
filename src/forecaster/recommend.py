"""Research lean from technical + optional heuristic evidence.

Ported from stock_predictor recommendation_engine scoring. Output is a lean with
confidence and risk, never an order: no targets, stops, or buy/sell wording.
"""

from __future__ import annotations

from typing import Literal

from .models import Contract, MarketEvent, ResearchPrediction, Score
from .technical import TechnicalSnapshot

Lean = Literal["bullish", "neutral", "bearish"]
Risk = Literal["low", "moderate", "high", "very_high"]
LIMITATIONS = (
    "Uncalibrated heuristic research lean; not financial advice.",
    "Not a trade instruction; no price targets or order guidance is produced.",
    "Close-only technicals; no volume, ATR or fundamental data are used.",
)
_RISK_ORDER = ("low", "moderate", "high", "very_high")


class ResearchLean(Contract):
    symbol: str
    status: Literal["completed", "insufficient_evidence"]
    lean: Lean | None = None
    confidence: Score = 0
    risk_level: Risk | None = None
    technical_score: float | None = None
    sentiment_score: float | None = None
    overall_score: float | None = None
    reasoning: tuple[str, ...] = ()
    limitations: tuple[str, ...] = LIMITATIONS


class RankedLean(Contract):
    rank: int
    lean: ResearchLean
    rank_score: float


def technical_score(t: TechnicalSnapshot) -> float | None:
    """0-100; None when no indicator is available (never a neutral guess)."""
    parts: list[float] = []
    if t.rsi_14 is not None:
        parts.append(80 if 30 <= t.rsi_14 <= 70 else 75 if t.rsi_14 < 30 else 40)
    if t.macd is not None and t.macd_signal is not None:
        parts.append(75 if t.macd > t.macd_signal else 45)
    if None not in (t.sma_20, t.sma_50, t.sma_200):
        if t.sma_20 > t.sma_50 > t.sma_200:
            parts.append(85)
        elif t.sma_20 > t.sma_50:
            parts.append(70)
        elif t.sma_20 < t.sma_50 < t.sma_200:
            parts.append(25)
        else:
            parts.append(50)
    if t.trend != "insufficient_data":
        parts.append({"bullish": 70, "sideways": 50, "bearish": 30}[t.trend])
    if t.annualized_volatility_pct is not None:
        v = t.annualized_volatility_pct
        parts.append(75 if v < 20 else 60 if v < 40 else 35)
    return sum(parts) / len(parts) if parts else None


def sentiment_score(
    prediction: ResearchPrediction | None, events: tuple[MarketEvent, ...] = ()
) -> float | None:
    """0-100 from heuristic horizons and event strength; None when neither exists."""
    parts: list[float] = []
    if prediction is not None and prediction.status in {"completed", "partial"}:
        hs = [h for h in prediction.horizons if h.coverage > 0]
        if hs:
            # Low-coverage horizons blend toward neutral rather than toward zero.
            parts.append(
                sum(50 + h.coverage * (h.heuristic_score * 100 - 50) for h in hs)
                / len(hs)
            )
    evs = events or (prediction.events if prediction is not None else ())
    if evs:
        parts.append(50 + 50 * sum(e.signed_strength for e in evs) / len(evs))
    return max(0.0, min(100.0, sum(parts) / len(parts))) if parts else None


def assess_risk(t: TechnicalSnapshot, sentiment_coverage: float | None) -> Risk:
    factors: list[int] = []
    v = t.annualized_volatility_pct
    if v is not None:
        factors.append(4 if v > 50 else 3 if v > 30 else 2 if v > 15 else 1)
    if sentiment_coverage is not None:
        c = sentiment_coverage
        factors.append(4 if c < 0.4 else 3 if c < 0.6 else 2 if c < 0.8 else 1)
    if not factors:
        return "high"  # unknown risk is reported conservatively
    avg = sum(factors) / len(factors)
    return _RISK_ORDER[3 if avg >= 3.5 else 2 if avg >= 2.5 else 1 if avg >= 1.5 else 0]


def confidence(scores: list[float]) -> float:
    """Agreement between component scores plus distance from neutral, clamped to .1-.95."""
    strength = abs(sum(scores) / len(scores) - 50) / 50
    if len(scores) < 2:  # single source cannot show agreement
        return max(0.1, min(0.95, strength * 0.4))
    agreement = max(0.0, (50 - (max(scores) - min(scores))) / 50)
    return max(0.1, min(0.95, agreement * 0.6 + strength * 0.4))


def research_lean(
    technical: TechnicalSnapshot | None,
    prediction: ResearchPrediction | None = None,
    events: tuple[MarketEvent, ...] = (),
) -> ResearchLean:
    symbol = (
        technical.symbol
        if technical
        else (prediction.snapshot.symbol if prediction else "UNKNOWN")
    )
    t_score = technical_score(technical) if technical else None
    s_score = sentiment_score(prediction, events)
    if t_score is None:
        why = "Technical evidence unavailable or too short" + (
            "; sentiment evidence missing too." if s_score is None else "."
        )
        return ResearchLean(
            symbol=symbol,
            status="insufficient_evidence",
            sentiment_score=s_score,
            reasoning=(why,),
        )
    scores = [t_score] + ([s_score] if s_score is not None else [])
    overall = sum(scores) / len(scores)
    lean: Lean = (
        "bullish" if overall >= 65 else "bearish" if overall <= 40 else "neutral"
    )
    coverage = None
    if prediction is not None and prediction.horizons:
        coverage = sum(h.coverage for h in prediction.horizons) / len(
            prediction.horizons
        )
    reasoning = [
        f"Technical score {t_score:.0f}/100 from {technical.bar_count} close-only bars "
        f"(trend: {technical.trend}).",
    ]
    if s_score is None:
        reasoning.append(
            "Sentiment/event evidence missing; lean rests on technicals alone and confidence is reduced."
        )
    else:
        reasoning.append(
            f"Sentiment score {s_score:.0f}/100 from heuristic horizons and events."
        )
    if technical.status == "partial":
        reasoning.append("Under 200 bars; long-term trend averages unavailable.")
    conf = confidence(scores)
    if s_score is None:
        conf = min(conf, 0.5)
    return ResearchLean(
        symbol=symbol,
        status="completed",
        lean=lean,
        confidence=conf,
        risk_level=assess_risk(technical, coverage),
        technical_score=t_score,
        sentiment_score=s_score,
        overall_score=overall,
        reasoning=tuple(reasoning),
    )


_LEAN_BONUS = {"bullish": 10, "neutral": 0, "bearish": -10}


def rank_leans(leans: list[ResearchLean]) -> tuple[RankedLean, ...]:
    """Order completed leans by score + confidence + lean bonus; skips insufficient ones."""
    scored = [
        (x.overall_score + x.confidence * 20 + _LEAN_BONUS[x.lean], x)
        for x in leans
        if x.status == "completed"
    ]
    scored.sort(key=lambda p: (-p[0], p[1].symbol))
    return tuple(
        RankedLean(rank=i, lean=x, rank_score=s) for i, (s, x) in enumerate(scored, 1)
    )


def select_diversified(
    ranked: tuple[RankedLean, ...],
    *,
    max_items: int = 10,
    max_risk: Risk = "high",
    min_confidence: float = 0.3,
    max_per_theme: int = 2,
    themes: dict[str, str] | None = None,
) -> tuple[RankedLean, ...]:
    """Watchlist selection capped per theme (e.g. layer/sector); not a portfolio allocation."""
    themes = themes or {}
    counts: dict[str, int] = {}
    out: list[RankedLean] = []
    for item in ranked:
        x = item.lean
        if x.confidence < min_confidence or _RISK_ORDER.index(
            x.risk_level
        ) > _RISK_ORDER.index(max_risk):
            continue
        theme = themes.get(x.symbol, x.symbol)
        if counts.get(theme, 0) >= max_per_theme:
            continue
        counts[theme] = counts.get(theme, 0) + 1
        out.append(item)
        if len(out) >= max_items:
            break
    return tuple(out)
