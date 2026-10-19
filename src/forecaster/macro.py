"""Selected original FRED regime thresholds; latest data is not an ALFRED vintage."""

import asyncio
import math
from datetime import date, datetime, timezone

from .models import ContextSignal, EvidenceCard, ProviderIssue, ProviderResult
from .providers import ProviderFailure, unavailable


class MacroSignalService:
    SERIES = {
        "FEDFUNDS": "Effective Federal Funds Rate",
        "DGS10": "10-Year Treasury Yield",
        "T10Y2Y": "10-Year Minus 2-Year Treasury",
        "CPIAUCSL": "Consumer Price Index",
        "UNRATE": "Civilian Unemployment Rate",
        "BAMLH0A0HYM2": "High Yield Option-Adjusted Spread",
    }

    def __init__(self, providers):
        self.providers = providers

    async def get_macro_snapshot(self, request):
        p = self.providers
        if request.as_of is not None:
            return unavailable("fred", "unsupported_as_of",
                               "Latest revised FRED observations are not historical vintages.", "unsupported")
        if not p.settings.fred_api_key.get_secret_value():
            return unavailable("fred", "not_configured", "FRED API key is not configured.")
        cached = p.cache.get("macro")
        if cached is not None:
            return cached

        async def fetch(series):
            try:
                payload = await p._json("fred", params={
                    "series_id": series, "api_key": p.settings.fred_api_key.get_secret_value(),
                    "file_type": "json", "sort_order": "desc", "limit": "3",
                })
                for item in payload["observations"]:
                    if item.get("value") in {None, "."}:
                        continue
                    value = float(item["value"])
                    observed = date.fromisoformat(item["date"])
                    if not math.isfinite(value) or observed > p.clock().date():
                        raise ValueError("invalid observation")
                    return series, (value, observed), None
                return series, None, ProviderIssue(
                    source_id="fred", code="incomplete", message=f"No observation for {series}.")
            except ProviderFailure as exc:
                return series, None, exc.issue
            except (KeyError, TypeError, ValueError):
                return series, None, ProviderIssue(
                    source_id="fred", code="schema", message=f"Invalid observation for {series}.")

        items = await asyncio.gather(*(fetch(series) for series in self.SERIES))
        values = {series: item for series, item, _ in items if item is not None}
        issues = tuple(issue for _, _, issue in items if issue is not None)
        if not values:
            return ProviderResult(status="unavailable", issues=issues)
        score, notes = self.summarize({key: value[0] for key, value in values.items()})
        now = p.clock()
        evidence = tuple(EvidenceCard(
            source_id="fred", source_type="macro", title=self.SERIES[series],
            summary=f"{series} latest revised observation: {value:g}, observation date {observed}.",
            observed_at=datetime.combine(observed, datetime.min.time(), timezone.utc),
            retrieved_at=now, confidence=.8,
        ) for series, (value, observed) in values.items())
        return p.cache.set("macro", ProviderResult[ContextSignal](
            status="partial" if issues else "completed", issues=issues,
            value=ContextSignal(source_id="fred", score=score, evidence=evidence, notes=notes),
        ))

    @staticmethod
    def summarize(values):
        score, notes = 0.0, []
        # Thresholds retained from stock_predictor.macro_signal_service.
        rules = (
            ("FEDFUNDS", lambda x: (-.2, "Policy rates remain restrictive.") if x >= 4.5
             else (.08, "Policy rates comparatively supportive.") if x <= 2.5 else (0, "")),
            ("T10Y2Y", lambda x: (-.22, "Yield curve inverted.") if x < 0
             else (.08, "Yield curve positively sloped.") if x > 1 else (0, "")),
            ("UNRATE", lambda x: (.08, "Labor market relatively firm.") if x <= 4.5
             else (-.12, "Unemployment elevated.") if x >= 5.5 else (0, "")),
            ("BAMLH0A0HYM2", lambda x: (-.18, "High yield spreads wide.") if x >= 4.5
             else (.08, "Credit spreads contained.") if x <= 3.2 else (0, "")),
            ("DGS10", lambda x: (-.08, "Long-end yields elevated.") if x >= 4.5 else (0, "")),
        )
        for series, rule in rules:
            if series in values:
                change, note = rule(values[series])
                score += change
                if note:
                    notes.append(note)
        if "CPIAUCSL" in values:
            notes.append("CPI level tracked; level alone is not an inflation rate.")
        return max(-1, min(1, score)), tuple(notes)
