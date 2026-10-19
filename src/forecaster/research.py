"""Requested-symbol research migrated from stock_predictor.research_prediction.

Retains trend/volatility features and weighted 5/21-session heuristics, not
the original probability labels, synthetic evidence or event-count bonus.
"""

import asyncio
from datetime import datetime, timezone
from statistics import mean, pstdev

from .events import EventExtractionService
from .models import (
    EvidenceSnapshot, HorizonPrediction, ProviderIssue, ProviderResult,
    ResearchPrediction, ResearchRequest,
)
from .providers import ForecastSettings, PublicDataProviders
from .source_registry import SourceRegistry
from .themes import AIInfrastructureThemeModel


def clamp(value, low, high):
    return max(low, min(high, value))


class ResearchPredictionService:
    def __init__(self, providers=None, settings=None):
        self.settings = settings or (providers.settings if providers is not None else ForecastSettings())
        self.providers = providers or PublicDataProviders(settings=self.settings)

    async def research(self, request: ResearchRequest) -> ResearchPrediction:
        request = ResearchRequest.model_validate(request)
        started = datetime.now(timezone.utc)
        if request.as_of is not None:
            snapshot = EvidenceSnapshot(
                symbol=request.symbol, as_of=min(started, request.as_of), created_at=started,
                evidence=(), issues=(ProviderIssue(
                    source_id="yahoo-chart", code="unsupported_as_of",
                    message="Historical point-in-time research is unsupported for these adapters.",
                ),),
            )
            return ResearchPrediction(
                snapshot=snapshot, status="unsupported", horizons=(), events=(),
                theme_exposures=(), source_provenance=(),
                limitations=("No leakage-safe historical backtest is provided.",),
            )
        methods = (("yahoo-chart", self.providers.market),
                   ("fred", self.providers.macro), ("sec-edgar", self.providers.filings))

        async def fetch(source, method):
            try:
                result = await method(request)
                if not isinstance(result, ProviderResult):
                    raise ValueError("provider result contract required")
                return result
            except Exception:
                return ProviderResult(status="unavailable", issues=(ProviderIssue(
                    source_id=source, code="unavailable", message="Provider operation failed.",
                ),))

        tasks = [asyncio.create_task(fetch(source, method)) for source, method in methods]
        done, pending = await asyncio.wait(tasks, timeout=self.settings.research_deadline_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        results = []
        for (source, _), task in zip(methods, tasks):
            results.append(task.result() if task in done else ProviderResult(
                status="unavailable", issues=(ProviderIssue(
                    source_id=source, code="budget", message="Research aggregate deadline exhausted.",
                ),),
            ))
        market_result, macro, filings = results
        market = market_result.value
        issues = tuple(issue for result in results for issue in result.issues)
        theme_model = AIInfrastructureThemeModel()
        exposures = theme_model.get_exposures(request.symbol)
        now = datetime.now(timezone.utc)
        evidence = tuple(card for result in results if result.value is not None
                         for card in result.value.evidence)
        evidence += tuple(theme_model.evidence_for(exposure, now) for exposure in exposures)
        evidence = tuple({card.evidence_id: card for card in evidence}.values())
        snapshot = EvidenceSnapshot(
            symbol=request.symbol, as_of=now, created_at=now, market=market,
            evidence=evidence, issues=issues, sessions=request.sessions,
        )
        events = EventExtractionService().extract(evidence)
        limitations = (
            "Scores and return estimates are uncalibrated heuristics, not probabilities.",
            "No live news adapter, analyst fundamentals or automatic exchange calendar is included.",
            "Latest revised FRED/SEC data do not support point-in-time historical research.",
            "Current-session closes require a caller-attested official exchange sequence; otherwise excluded.",
        )
        if market is None or len(market.bars) < 5:
            return ResearchPrediction(
                snapshot=snapshot, status="abstained", horizons=(), events=events,
                theme_exposures=exposures,
                source_provenance=SourceRegistry().provenance_for(c.source_id for c in evidence),
                limitations=limitations + ("At least five completed daily prices required.",),
            )
        closes = [(bar.adjusted_close if market.price_convention == "split_dividend_adjusted_close"
                   else bar.close) for bar in market.bars]
        trend = clamp(((closes[-1] - closes[0]) / closes[0]) * 3, -1, 1)
        returns = [(new - old) / old for old, new in zip(closes, closes[1:])]
        volatility = clamp(pstdev(returns) * 16, .05, 1)
        theme = mean(exposure.score for exposure in exposures) if exposures else 0.0
        macro_score = macro.value.score if macro.value else 0.0
        filing_score = filings.value.score if filings.value else 0.0
        # Only genuine news catalysts contribute; macro/market summaries and
        # filing recency are already represented in their own signal families.
        news_ids = {card.evidence_id for card in evidence if card.source_type == "news"}
        news_events = [event for event in events if event.evidence_id in news_ids]
        event_strength = sum(event.signed_strength for event in news_events[:5])
        coverage = sum(result.value is not None for result in results) / len(results)
        horizons = []
        for horizon in request.horizons:
            multiplier, theme_weight = (.75, .7) if horizon == 5 else (1.25, 1.0)
            signal = (.5 + .12 * trend + .15 * theme * theme_weight
                      + .06 * macro_score + .06 * filing_score
                      + .03 * event_strength - .09 * volatility)
            score = clamp(signal, .08, .92)
            if coverage < 2 / 3:
                score = clamp(score, .42, .58)
            downside = clamp(.28 + .42 * volatility - .08 * theme - .02 * filing_score, .05, .9)
            horizons.append(HorizonPrediction(
                horizon_sessions=horizon, heuristic_score=score, downside_score=downside,
                coverage=coverage, outlook="bullish" if score >= .57 else "bearish" if score <= .43 else "mixed",
                expected_excess_return_pct=(score - .5) * 10 * multiplier,
                reasoning=(f"Price trend {trend:.3f}; volatility penalty {volatility:.3f}.",
                           f"Curated theme {theme:.3f}; macro {macro_score:.3f}; filing recency {filing_score:.3f}.",
                           f"Available provider families {round(coverage * 3)}/3; missing inputs contribute no evidence."),
            ))
        return ResearchPrediction(
            snapshot=snapshot, status="partial" if issues else "completed",
            horizons=tuple(horizons), events=events, theme_exposures=exposures,
            source_provenance=SourceRegistry().provenance_for(c.source_id for c in evidence),
            limitations=limitations,
        )
