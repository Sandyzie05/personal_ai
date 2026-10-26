"""Typed contracts for inter-agent traffic.

`extra="forbid"` everywhere: an unexpected field is a disclosure bug, never a feature.
"""

import math
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Trust = Literal["trusted", "untrusted"]  # untrusted = derived from email/portal/web content
Status = Literal["ok", "uncertain", "unavailable", "unresolved"]

# Printable, instruction-hostile allowlist for free-text fields that come from untrusted sources.
SAFE_TEXT = r"^[A-Za-z0-9 .,&'\-/()$%]{0,80}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Finding(Strict):
    agent: str
    claim_key: str = Field(max_length=80)
    value: str | float | None = None
    summary: str = Field(max_length=500)
    evidence_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    status: Status = "ok"
    trust: Trust = "trusted"
    as_of: Optional[AwareDatetime] = None  # naive datetimes are rejected: staleness math needs UTC-aware

    @field_validator("value")
    @classmethod
    def _finite(cls, v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("non-finite value")
        return v


class PlanStep(Strict):
    id: str
    agent: str
    goal: str = Field(max_length=300)
    depends_on: tuple[str, ...] = ()
    symbol: Optional[str] = Field(default=None, pattern=r"^[A-Z]{1,5}([.-][A-Z])?$")
    category: Optional[Literal["electricity", "gas", "mobile", "credit_card", "checking", "brokerage"]] = None
    timeout_s: float = Field(default=30, gt=0, le=300)


class Plan(Strict):
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=8)


# --- Per-edge bus payloads: only what the receiver needs for its task. ---

class CashflowDelta(Strict):  # expense -> market
    period: str = Field(pattern=r"^\d{4}(-Q[1-4]|-\d{2})$")
    monthly_cashflow_delta: float = Field(ge=-1e7, le=1e7)
    direction: Literal["up", "down", "flat"]


class SymbolRequest(Strict):  # coordinator -> forecaster
    symbol: str = Field(pattern=r"^[A-Z]{1,5}([.-][A-Z])?$")
    horizon_sessions: Literal[5, 21] = 5


class HoldingsSummary(Strict):  # coordinator -> market (no account ids, no lot-level data)
    symbols: tuple[Annotated[str, Field(pattern=r"^[A-Z]{1,5}([.-][A-Z])?$")], ...] = Field(max_length=25)


# --- Quarantine output schemas (untrusted -> structured, no free text of consequence). ---

class EmailFacts(Strict):
    sender_domain: str = Field(pattern=r"^(?:[a-z0-9-]{1,30}\.){1,4}[a-z]{2,10}$")
    category: Literal["bill", "statement", "receipt", "alert", "other"]
    amount: Optional[float] = None
    due_date: Optional[date] = None
    # Closed vocabulary, not free text: free prose from an email must never reach a summary/prompt.
    topic: Literal["", "electricity", "gas", "water", "internet", "mobile", "credit_card", "bank",
                   "brokerage", "insurance", "subscription", "other"] = ""


class StatementFacts(Strict):
    provider: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 .&'-]{0,39}$")
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_due: Optional[float] = None


EDGES = {
    ("expense", "market"): CashflowDelta,
    ("coordinator", "forecaster"): SymbolRequest,
    ("coordinator", "market"): HoldingsSummary,
    ("email", "coordinator"): EmailFacts,
    ("portal", "expense"): StatementFacts,
}
