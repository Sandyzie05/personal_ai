"""F7 news analyst (slot forecast.news_analyst): one quarantined extract_facts call per headline.

Only closed enums leave this module: per-ticker counts and a per-story sentiment keyed by the code-made story_id.
Headline text never reaches another prompt, a summary or markdown (invariants S1, S16)."""

from time import monotonic
from typing import Any, Callable, Dict, Literal

from pydantic import Field

from src.agents.orchestration.quarantine import extract_facts
from src.agents.orchestration.schemas import NewsFacts
from src.config import FORECAST_SPECIALIST_CALL_SECONDS

from .models import Contract
from src.observability import otel

Sentiment = Literal["positive", "neutral", "negative"]


class TickerCounts(Contract):
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    high_materiality: int = 0


class NewsAnalysis(Contract):
    counts: Dict[str, TickerCounts]
    story_sentiment: Dict[str, Sentiment]  # story_id -> AI sentiment
    analyzed: int
    rejected: int  # schema/injection/unavailable: no facts, nothing invented
    flagged: int  # input looked like an injection attempt
    skipped: int = Field(default=0, description="not attempted: time budget ran out")


@otel.traced("forecast.news_analyst")
def analyze(
    stories,
    client_factory: Callable[..., Any],
    agent: Dict[str, Any],
    limit: int = 10,
    budget_seconds: float = FORECAST_SPECIALIST_CALL_SECONDS,
) -> NewsAnalysis:
    deadline = monotonic() + budget_seconds
    counts: Dict[str, Dict[str, int]] = {}
    sentiment: Dict[str, str] = {}
    rejected = flagged = skipped = 0
    for story in list(stories)[:limit]:
        remaining = deadline - monotonic()
        if remaining <= 0:
            skipped += 1
            continue
        client = client_factory(remaining_seconds=remaining)
        client.temperature = min(max(float(agent.get("temperature", 0.0)), 0.0), 1.0)
        client.max_tokens = 120
        if agent.get("model") and hasattr(client, "model"):
            client.model = str(agent["model"])
        ex = extract_facts(
            f"{story.title} — {story.publisher}",
            "yahoo-news",
            NewsFacts,
            client,
            max_chars=400,
            extra_instructions=str(agent.get("extra_instructions") or ""),
        )
        flagged += bool(ex.flagged)
        if ex.facts is None:
            rejected += 1
            continue
        facts = ex.facts
        # The model may only pick a ticker the feed already linked to this story; otherwise use the feed's tickers.
        tickers = (
            [facts.ticker]
            if facts.ticker in story.related_tickers
            else list(story.related_tickers[:3]) or ["MARKET"]
        )
        sentiment[story.story_id] = facts.sentiment
        for t in tickers:
            row = counts.setdefault(
                t, {"positive": 0, "neutral": 0, "negative": 0, "high_materiality": 0}
            )
            row[facts.sentiment] += 1
            row["high_materiality"] += facts.materiality == "high"
    return NewsAnalysis(
        counts={t: TickerCounts(**c) for t, c in counts.items()},
        story_sentiment=sentiment,
        analyzed=len(sentiment),
        rejected=rejected,
        flagged=flagged,
        skipped=skipped,
    )
