"""Lightweight provider health + accuracy summary, replacing stock_predictor monitoring.

In-memory and pure: callers feed ProviderResults (with measured latency) in and
read a snapshot out. No server, psutil, or persistence.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .models import ProviderResult, utc
from .report import Pair, summarize

FAILED = {"unavailable", "unsupported"}
DOWN_AFTER = 3


@dataclass
class ProviderHealth:
    source_id: str
    calls: int = 0
    failures: int = 0
    partials: int = 0
    consecutive_failures: int = 0
    last_status: str | None = None
    last_latency_ms: float | None = None
    last_at: datetime | None = None
    last_issue_codes: tuple[str, ...] = ()
    issue_counts: dict[str, int] = field(default_factory=dict)

    @property
    def state(self) -> str:
        if self.calls == 0:
            return "unknown"
        if self.consecutive_failures >= DOWN_AFTER:
            return "down"
        return "healthy" if self.last_status == "completed" else "degraded"

    def to_api(self) -> dict:
        return {
            "source_id": self.source_id,
            "state": self.state,
            "calls": self.calls,
            "failures": self.failures,
            "partials": self.partials,
            "consecutive_failures": self.consecutive_failures,
            "last_status": self.last_status,
            "last_latency_ms": self.last_latency_ms,
            "last_at": self.last_at.isoformat() if self.last_at else None,
            "last_issue_codes": list(self.last_issue_codes),
            "issue_counts": dict(self.issue_counts),
        }


class HealthTracker:
    def __init__(self):
        self._providers: dict[str, ProviderHealth] = {}

    def record(
        self,
        source_id: str,
        result: ProviderResult,
        *,
        at: datetime,
        latency_ms: float | None = None,
    ) -> ProviderHealth:
        # source_id is explicit: completed results carry no issues to infer it from.
        item = self._providers.setdefault(source_id, ProviderHealth(source_id))
        item.calls += 1
        item.last_status = result.status
        item.last_latency_ms = None if latency_ms is None else round(latency_ms, 2)
        item.last_at = utc(at)
        item.last_issue_codes = tuple(issue.code for issue in result.issues)
        for issue in result.issues:
            item.issue_counts[issue.code] = item.issue_counts.get(issue.code, 0) + 1
        item.partials += result.status == "partial"
        if result.status in FAILED:
            item.failures += 1
            item.consecutive_failures += 1
        else:
            item.consecutive_failures = 0
        return item

    def snapshot(
        self, pairs: list[Pair] | None = None, *, as_of: datetime | None = None
    ) -> dict:
        providers = [self._providers[key].to_api() for key in sorted(self._providers)]
        states = {p["state"] for p in providers}
        overall = (
            "unknown"
            if not providers
            else "down"
            if states == {"down"}
            else "healthy"
            if states == {"healthy"}
            else "degraded"
        )
        return {
            "status": overall,
            "providers": providers,
            "accuracy": summarize(pairs or [], as_of=as_of),
        }
