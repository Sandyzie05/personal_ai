"""Pure session-aware outcome contracts, replacing prediction_tracker's quote fallback.

The caller supplies a complete official exchange-session sequence. This module
validates order/identity, not exchange holidays. No storage or quote fetching.
"""

import hashlib
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .models import (
    Contract, Exchange, Horizon, Positive, PriceConvention, ResearchPrediction,
    SourceId, TradingSession, canonical_symbol, utc,
)

class SessionPrice(Contract):
    symbol: str
    exchange: Literal["XNYS", "XNAS", "ARCX"]
    session: date
    close: Positive
    adjusted_close: Positive | None = None
    source_id: Literal["yahoo-chart", "alpha-vantage"]
    retrieved_at: datetime

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)

    @model_validator(mode="after")
    def observed(self):
        if self.session > self.retrieved_at.astimezone(ZoneInfo("America/New_York")).date():
            raise ValueError("price session exceeds retrieval date")
        return self


class ForecastRecord(Contract):
    version: Literal["session-forecast-v1"] = "session-forecast-v1"
    scorer_version: str
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    as_of: datetime
    source_ids: tuple[SourceId, ...]
    horizon_sessions: Horizon
    origin: TradingSession
    target: TradingSession | None
    schedule_status: Literal["scheduled", "session_unavailable"]
    session_sequence: tuple[TradingSession, ...] = Field(min_length=1, max_length=512)
    price_convention: PriceConvention
    origin_price: Positive
    benchmark_symbol: str = "SPY"
    benchmark_exchange: Literal["XNYS", "XNAS", "ARCX"] = "ARCX"
    expected_excess_return_pct: float = Field(allow_inf_nan=False)
    target_definition: Literal["last_completed_close_plus_n_supplied_sessions"] = "last_completed_close_plus_n_supplied_sessions"
    calendar_basis: Literal["caller_supplied_official_sequence_unverified"] = "caller_supplied_official_sequence_unverified"

    _symbol = field_validator("symbol", "benchmark_symbol", mode="before")(canonical_symbol)

    @model_validator(mode="after")
    def identities(self):
        sequence = self.session_sequence
        if any(item.exchange != self.origin.exchange for item in sequence):
            raise ValueError("mixed exchange session sequence")
        days = [item.session for item in sequence]
        closes = [item.closes_at for item in sequence]
        if days != sorted(set(days)) or closes != sorted(set(closes)):
            raise ValueError("sessions must be unique and chronological")
        completed = [item for item in sequence if item.closes_at <= self.as_of]
        if not completed or completed[-1] != self.origin:
            raise ValueError("origin must be latest supplied completed close at cutoff")
        target_index = sequence.index(self.origin) + self.horizon_sessions
        expected = sequence[target_index] if target_index < len(sequence) else None
        if self.target != expected or self.schedule_status != (
            "scheduled" if expected else "session_unavailable"
        ):
            raise ValueError("target does not match explicit session offset")
        if self.symbol == self.benchmark_symbol:
            raise ValueError("benchmark must differ from requested symbol")
        return self

    @property
    def forecast_id(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class OutcomeEvaluation(Contract):
    version: Literal["session-outcome-v1"] = "session-outcome-v1"
    forecast_id: str
    snapshot_id: str
    horizon_sessions: Horizon
    target_session: date | None
    evaluated_at: datetime
    price_convention: PriceConvention
    benchmark_symbol: str
    status: Literal["evaluated", "immature", "session_unavailable", "missing_data"]
    prices: tuple[SessionPrice, ...] = ()
    realized_return_pct: float | None = None
    benchmark_return_pct: float | None = None
    realized_excess_return_pct: float | None = None
    return_estimate_error_pct: float | None = None
    directional_match: bool | None = None
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def results(self):
        numbers = (self.realized_return_pct, self.benchmark_return_pct,
                   self.realized_excess_return_pct, self.return_estimate_error_pct)
        if self.status == "evaluated":
            if any(value is None for value in numbers) or len(self.prices) != 4:
                raise ValueError("evaluated outcome requires exact prices and returns")
        elif any(value is not None for value in numbers) or self.directional_match is not None:
            raise ValueError("unevaluated status cannot contain outcome metrics")
        return self

    @property
    def outcome_id(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def create_forecast(prediction: ResearchPrediction, horizon_sessions: Horizon, *,
                    sessions: tuple[TradingSession, ...], benchmark_symbol="SPY",
                    benchmark_exchange="ARCX") -> ForecastRecord:
    prediction = ResearchPrediction.model_validate(prediction)
    market = prediction.snapshot.market
    if market is None or prediction.status not in {"completed", "partial"}:
        raise ValueError("forecast requires usable research market evidence")
    horizon = next((h for h in prediction.horizons if h.horizon_sessions == horizon_sessions), None)
    if horizon is None:
        raise ValueError("requested horizon was not researched")
    sequence = tuple(TradingSession.model_validate(item) for item in sessions)
    if prediction.snapshot.sessions and sequence != prediction.snapshot.sessions:
        raise ValueError("forecast sequence differs from the research completion attestation")
    completed = [item for item in sequence if item.closes_at <= prediction.snapshot.as_of]
    if not completed:
        raise ValueError("no completed origin session in supplied sequence")
    origin = completed[-1]
    if origin.exchange != market.exchange or origin.session != market.bars[-1].session:
        raise ValueError("market latest close must match supplied origin identity")
    index = sequence.index(origin) + horizon_sessions
    target = sequence[index] if index < len(sequence) else None
    price = market.bars[-1]
    return ForecastRecord(
        scorer_version=prediction.scorer_version, snapshot_id=prediction.snapshot.snapshot_id,
        symbol=prediction.snapshot.symbol, as_of=prediction.snapshot.as_of,
        source_ids=tuple(dict.fromkeys(card.source_id for card in prediction.snapshot.evidence)),
        horizon_sessions=horizon_sessions, origin=origin, target=target,
        schedule_status="scheduled" if target else "session_unavailable",
        session_sequence=sequence, price_convention=market.price_convention,
        origin_price=(price.adjusted_close if market.price_convention == "split_dividend_adjusted_close"
                      else price.close),
        benchmark_symbol=benchmark_symbol, benchmark_exchange=benchmark_exchange,
        expected_excess_return_pct=horizon.expected_excess_return_pct,
    )


def evaluate_outcome(forecast: ForecastRecord, prices: tuple[SessionPrice, ...], *,
                     evaluated_at: datetime) -> OutcomeEvaluation:
    forecast = ForecastRecord.model_validate(forecast)
    evaluated_at = utc(evaluated_at)
    if evaluated_at < forecast.as_of:
        raise ValueError("evaluation precedes research cutoff")
    fields = dict(
        forecast_id=forecast.forecast_id, snapshot_id=forecast.snapshot_id,
        horizon_sessions=forecast.horizon_sessions,
        target_session=forecast.target.session if forecast.target else None,
        evaluated_at=evaluated_at, price_convention=forecast.price_convention,
        benchmark_symbol=forecast.benchmark_symbol,
    )
    if forecast.target is None:
        return OutcomeEvaluation(**fields, status="session_unavailable",
                                 notes=("Explicit exchange sequence does not include target session.",))
    if evaluated_at < forecast.target.closes_at:
        return OutcomeEvaluation(**fields, status="immature",
                                 notes=("Target session has not closed.",))
    prices = tuple(SessionPrice.model_validate(item) for item in prices)
    keys = [(price.symbol, price.exchange, price.session) for price in prices]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate price identities; do not select arbitrary revisions")
    by_key = dict(zip(keys, prices))
    required = (
        (forecast.symbol, forecast.origin.exchange, forecast.origin.session),
        (forecast.symbol, forecast.target.exchange, forecast.target.session),
        (forecast.benchmark_symbol, forecast.benchmark_exchange, forecast.origin.session),
        (forecast.benchmark_symbol, forecast.benchmark_exchange, forecast.target.session),
    )
    selected = tuple(by_key[key] for key in required if key in by_key)
    if len(selected) != 4:
        return OutcomeEvaluation(**fields, status="missing_data",
                                 notes=("Exact origin/target prices for symbol and benchmark required; no fallback.",))
    for index, price in enumerate(selected):
        close_time = forecast.origin.closes_at if index in {0, 2} else forecast.target.closes_at
        if price.retrieved_at > evaluated_at or price.retrieved_at < close_time:
            return OutcomeEvaluation(**fields, status="missing_data",
                                     notes=("Prices must be retrieved after their close and before evaluation.",))
    values = tuple(price.adjusted_close if forecast.price_convention == "split_dividend_adjusted_close"
                   else price.close for price in selected)
    if any(value is None for value in values):
        return OutcomeEvaluation(**fields, status="missing_data",
                                 notes=("Declared adjusted-price convention missing; no unadjusted substitution.",))
    origin, target, benchmark_origin, benchmark_target = values
    # Retrospective corporate-action revisions must not rewrite a frozen base.
    if abs(origin - forecast.origin_price) > max(1e-8, abs(forecast.origin_price) * 1e-8):
        return OutcomeEvaluation(**fields, status="missing_data",
                                 notes=("Origin price differs from frozen forecast; adjustment basis requires reconciliation.",))
    realized = (target / origin - 1) * 100
    benchmark = (benchmark_target / benchmark_origin - 1) * 100
    excess = realized - benchmark
    expected = forecast.expected_excess_return_pct
    match = None if expected == 0 or excess == 0 else (expected > 0) == (excess > 0)
    return OutcomeEvaluation(
        **fields, status="evaluated", prices=selected,
        realized_return_pct=realized, benchmark_return_pct=benchmark,
        realized_excess_return_pct=excess, return_estimate_error_pct=excess - expected,
        directional_match=match,
        notes=("Same-convention close-to-close benchmark excess return; not simulated trade performance.",
               "Session completeness/benchmark close alignment supplied by caller, not holiday-verified.",
               "Baseline is last completed close at research cutoff, not a guaranteed executable entry."),
    )
