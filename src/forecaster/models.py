"""Immutable public research contracts, adapted from stock_predictor research_models."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Annotated, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Signal = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Horizon = Literal[5, 21]
Exchange = Literal["XNYS", "XNAS"]
PriceConvention = Literal["unadjusted_close", "split_dividend_adjusted_close"]
SourceId = Literal[
    "yahoo-chart", "yahoo-news", "alpha-vantage", "sec-edgar", "fred",
    "curated-ai-infrastructure-theme",
]


def canonical_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("symbol must be public US ticker text")
    value = value.upper()
    if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", value):
        raise ValueError("unsupported symbol; expected US ticker or share class")
    return value


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    @field_validator("*")
    @classmethod
    def normalize_times(cls, value):
        return utc(value) if isinstance(value, datetime) else value

    def to_api(self) -> dict:
        return self.model_dump(mode="json")


class TradingSession(Contract):
    exchange: Exchange
    session: date
    closes_at: datetime

    @model_validator(mode="after")
    def local_date(self):
        if self.closes_at.astimezone(ZoneInfo("America/New_York")).date() != self.session:
            raise ValueError("close timestamp must fall on declared New York session date")
        return self


def ordered_sessions(value):
    days = [item.session for item in value]
    closes = [item.closes_at for item in value]
    if (days != sorted(set(days)) or closes != sorted(set(closes))
            or len({item.exchange for item in value}) > 1):
        raise ValueError("sessions must be unique, chronological and from one exchange")
    return value


class ResearchRequest(Contract):
    symbol: str
    horizons: tuple[Horizon, ...] = (5, 21)
    as_of: datetime | None = None
    sessions: tuple[TradingSession, ...] = Field(default=(), max_length=512)

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)
    _sessions = field_validator("sessions")(ordered_sessions)

    @field_validator("horizons")
    @classmethod
    def unique_horizons(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("one or two distinct supported horizons required")
        return value


class DataSourceProfile(Contract):
    source_id: SourceId
    name: str
    categories: tuple[str, ...]
    caveats: str
    point_in_time: bool = False


class EvidenceCard(Contract):
    source_id: SourceId
    source_type: Literal["market", "news", "macro", "filing", "curated"]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    retrieved_at: datetime
    observed_at: datetime | None = None
    published_at: datetime | None = None
    available_at: datetime | None = None
    symbol: str | None = None
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    confidence: Score
    point_in_time: bool = False

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value):
        return canonical_symbol(value) if value is not None else None

    @model_validator(mode="after")
    def chronology(self):
        for value in (self.observed_at, self.published_at, self.available_at):
            if value is not None and value > self.retrieved_at:
                raise ValueError("evidence time exceeds retrieval time")
        if self.point_in_time and self.available_at is None:
            raise ValueError("point-in-time evidence requires availability time")
        return self

    @property
    def evidence_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class MarketEvent(Contract):
    event_type: str
    direction: Literal[
        "demand_up", "demand_down", "margin_up", "margin_down", "risk_up", "neutral"
    ]
    magnitude: Score
    evidence_id: str
    source_id: SourceId

    @property
    def signed_strength(self) -> float:
        if self.direction in {"demand_down", "margin_down", "risk_up"}:
            return -self.magnitude
        if self.direction in {"demand_up", "margin_up"}:
            return self.magnitude
        return 0.0


class ThemeExposure(Contract):
    theme_slug: str
    symbol: str
    layer: str
    score: Score
    drivers: tuple[str, ...]
    bottlenecks: tuple[str, ...]

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)


class PriceBar(Contract):
    symbol: str
    session: date
    close: Positive
    adjusted_close: Positive | None = None
    high: Positive | None = None
    low: Positive | None = None
    volume: Annotated[int, Field(ge=0)] | None = None
    source_id: Literal["yahoo-chart", "alpha-vantage"]
    retrieved_at: datetime

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)


class MarketData(Contract):
    symbol: str
    exchange: Exchange
    currency: Literal["USD"]
    bars: tuple[PriceBar, ...] = Field(min_length=1, max_length=252)
    price_convention: PriceConvention
    evidence: tuple[EvidenceCard, ...]

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)

    @model_validator(mode="after")
    def ordered_prices(self):
        sessions = [bar.session for bar in self.bars]
        if sessions != sorted(set(sessions)):
            raise ValueError("price sessions must be unique and chronological")
        if any(bar.symbol != self.symbol for bar in self.bars):
            raise ValueError("market symbol mismatch")
        if self.price_convention == "split_dividend_adjusted_close" and any(
            bar.adjusted_close is None for bar in self.bars
        ):
            raise ValueError("adjusted price convention requires every adjusted close")
        if not self.evidence:
            raise ValueError("market data requires source evidence")
        return self


class ProviderIssue(Contract):
    source_id: SourceId
    code: Literal[
        "unavailable", "transport", "timeout", "http", "schema", "budget",
        "unsupported_as_of", "not_configured", "incomplete",
    ]
    message: str = Field(min_length=1, max_length=200)


T = TypeVar("T")


class ProviderResult(Contract, Generic[T]):
    status: Literal["completed", "partial", "unavailable", "unsupported"]
    value: T | None = None
    issues: tuple[ProviderIssue, ...] = ()

    @model_validator(mode="after")
    def explicit_state(self):
        if (self.status in {"completed", "partial"}) != (self.value is not None):
            raise ValueError("provider status/value mismatch")
        if self.status != "completed" and not self.issues:
            raise ValueError("incomplete provider result requires reason")
        if self.status == "completed" and self.issues:
            raise ValueError("completed provider result cannot hide issues")
        return self


class ContextSignal(Contract):
    source_id: Literal["fred", "sec-edgar"]
    score: Signal
    evidence: tuple[EvidenceCard, ...] = Field(min_length=1)
    notes: tuple[str, ...]


class EvidenceSnapshot(Contract):
    symbol: str
    as_of: datetime
    created_at: datetime
    version: Literal["public-evidence-v1"] = "public-evidence-v1"
    historical_supported: bool = False
    sessions: tuple[TradingSession, ...] = Field(default=(), max_length=512)
    market: MarketData | None = None
    evidence: tuple[EvidenceCard, ...] = Field(max_length=24)
    issues: tuple[ProviderIssue, ...] = Field(default=(), max_length=32)

    _symbol = field_validator("symbol", mode="before")(canonical_symbol)
    _sessions = field_validator("sessions")(ordered_sessions)

    @model_validator(mode="after")
    def cutoff(self):
        if self.as_of > self.created_at:
            raise ValueError("cutoff exceeds snapshot creation")
        if self.market and self.market.symbol != self.symbol:
            raise ValueError("snapshot market symbol mismatch")
        if self.market and self.sessions and self.sessions[0].exchange != self.market.exchange:
            raise ValueError("snapshot schedule exchange mismatch")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence")
        for item in self.evidence:
            if item.retrieved_at > self.created_at:
                raise ValueError("evidence retrieved after snapshot creation")
            if item.symbol not in {None, self.symbol}:
                raise ValueError("snapshot evidence symbol mismatch")
            if self.historical_supported and (
                not item.point_in_time or item.available_at > self.as_of
            ):
                raise ValueError("historical availability not established")
        return self

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def content_hash(self) -> str:
        return self.snapshot_id


class HorizonPrediction(Contract):
    horizon_sessions: Horizon
    heuristic_score: Score
    downside_score: Score
    coverage: Score
    outlook: Literal["bullish", "mixed", "bearish"]
    expected_excess_return_pct: float = Field(allow_inf_nan=False)
    reasoning: tuple[str, ...]
    score_kind: Literal["uncalibrated_heuristic"] = "uncalibrated_heuristic"


class ResearchPrediction(Contract):
    snapshot: EvidenceSnapshot
    scorer_version: Literal["research-heuristic-v1"] = "research-heuristic-v1"
    status: Literal["completed", "partial", "abstained", "unsupported"]
    horizons: tuple[HorizonPrediction, ...]
    events: tuple[MarketEvent, ...]
    theme_exposures: tuple[ThemeExposure, ...]
    source_provenance: tuple[DataSourceProfile, ...]
    limitations: tuple[str, ...]
    disclaimer: str = "Uncalibrated research heuristic; not financial advice."
