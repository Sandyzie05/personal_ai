"""News search and 'today' report, adapted from stock_predictor.market_intelligence.

Headline text comes from a public feed and is UNTRUSTED: it is sanitised,
length-capped, and only ever exposed in fields next to ``trust="untrusted"``.
Derived fields (reasons, summaries) are templated and never embed headline text.
"""

from __future__ import annotations

import asyncio
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from .models import Contract, ProviderIssue, ProviderResult, canonical_symbol

TITLE_MAX, PUBLISHER_MAX, URL_MAX, MAX_TICKERS = 200, 80, 300, 8
DEFAULT_DEADLINE_SECONDS = 20.0
NOTE = (
    "Headlines are untrusted third-party text; keyword sentiment is a crude lean, "
    "not a forecast. Conflicting stories are reported as disagreement, not averaged."
)

_POSITIVE = (
    "beat",
    "growth",
    "surge",
    "boom",
    "build",
    "partnership",
    "expansion",
    "approval",
    "funding",
    "cooling",
    "easing",
    "cut",
)
_NEGATIVE = (
    "tariff",
    "sanction",
    "breach",
    "war",
    "shortage",
    "delay",
    "inflation",
    "hotter",
    "smuggled",
    "probe",
    "lawsuit",
    "outage",
    "recall",
)


@dataclass(frozen=True)
class TopicProfile:
    slug: str
    label: str
    query: str
    bullish_if_positive: tuple[str, ...]
    bearish_if_positive: tuple[str, ...]
    bullish_if_negative: tuple[str, ...]
    bearish_if_negative: tuple[str, ...]
    keywords: tuple[str, ...]


TOPICS = (
    TopicProfile(
        "ai-datacenter",
        "AI and Datacenter Buildout",
        "artificial intelligence datacenter",
        ("NVDA", "AVGO", "ANET", "VRT", "ETN", "MSFT", "AMZN", "NOW", "DDOG"),
        (),
        (),
        ("NVDA", "AVGO", "ANET", "VRT", "SMCI", "AMD"),
        ("ai", "datacenter", "gpu", "inference", "agent", "server"),
    ),
    TopicProfile(
        "chip-trade",
        "Semiconductor Trade and Export Controls",
        "semiconductor export controls",
        ("INTC", "GFS", "AMAT"),
        ("NVDA", "AMD", "TSM", "ASML", "AAPL"),
        ("NVDA", "AMD", "TSM", "ASML"),
        ("INTC", "GFS"),
        ("semiconductor", "export", "tariff", "foundry", "smuggled", "chip"),
    ),
    TopicProfile(
        "rates-inflation",
        "Rates, Inflation, and Fed Policy",
        "federal reserve inflation",
        ("HD", "LOW", "DHI", "MA", "V", "PYPL", "AAPL", "AMZN"),
        ("JPM", "BAC", "BRK.B"),
        ("JPM", "BAC", "BRK.B", "XOM"),
        ("HD", "LOW", "DHI", "NOW", "PLTR", "AMZN"),
        ("fed", "inflation", "rates", "mortgage", "yield", "cpi"),
    ),
    TopicProfile(
        "energy-supply",
        "Oil, Shipping, and Global Supply",
        "oil supply opec shipping disruption",
        ("XOM", "CVX", "SLB", "HAL"),
        ("DAL", "UAL", "FDX", "UPS", "AAL"),
        ("DAL", "UAL", "FDX", "UPS"),
        ("XOM", "CVX"),
        ("oil", "opec", "shipping", "supply", "crude", "disruption"),
    ),
    TopicProfile(
        "cybersecurity",
        "Cybersecurity and Platform Resilience",
        "cybersecurity breach enterprise software",
        ("CRWD", "PANW", "ZS", "NET", "DDOG"),
        (),
        (),
        ("NOW", "CRM", "MSFT", "ORCL"),
        ("cybersecurity", "breach", "ransomware", "outage", "hack"),
    ),
    TopicProfile(
        "geopolitics-defense",
        "Defense and Geopolitics",
        "defense spending geopolitics",
        ("RTX", "LMT", "NOC", "GD"),
        (),
        (),
        ("DAL", "UAL", "BKNG"),
        ("defense", "missile", "war", "conflict", "geopolitics"),
    ),
)


def clean_text(value, cap):
    """Drop control/format/private chars, collapse whitespace, cap length."""
    text = "".join(
        " " if ch.isspace() else ch
        for ch in str(value or "")
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    return " ".join(text.split())[:cap]


class NewsItem(Contract):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    publisher: str = Field(max_length=PUBLISHER_MAX)
    url: str | None = Field(default=None, max_length=URL_MAX)
    published_at: datetime
    related_tickers: tuple[str, ...] = Field(default=(), max_length=MAX_TICKERS)
    trust: Literal["untrusted"] = "untrusted"


class LinkedStock(Contract):
    symbol: str
    bias: Literal["up", "down", "mixed"]
    reason: str  # templated; never contains headline text


class Story(Contract):
    story_id: str
    title: str
    publisher: str
    url: str | None
    published_at: datetime
    topic_slug: str
    sentiment: Literal["positive", "neutral", "negative"]
    directional_bias: Literal["up", "down", "mixed"]
    impact_score: float
    related_tickers: tuple[str, ...]
    linked_stocks: tuple[LinkedStock, ...]
    trust: Literal["untrusted"] = "untrusted"


class StockLean(Contract):
    symbol: str
    score: float
    story_ids: tuple[str, ...]


class Disagreement(Contract):
    symbol: str
    up_score: float
    down_score: float
    up_story_ids: tuple[str, ...]
    down_story_ids: tuple[str, ...]
    stronger_side: Literal["up", "down", "balanced"]


class NewsReport(Contract):
    as_of: datetime
    stories: tuple[Story, ...]
    bullish: tuple[StockLean, ...]
    bearish: tuple[StockLean, ...]
    disagreements: tuple[Disagreement, ...]
    topics_failed: tuple[str, ...] = ()
    trust: Literal["untrusted"] = "untrusted"
    note: str = NOTE


def parse_news(payload, now):
    """Validate a Yahoo search payload into sanitised items; schema errors raise."""
    items, seen = [], set()
    for raw in payload["news"]:
        if not isinstance(raw, dict):
            continue
        title = clean_text(raw.get("title"), TITLE_MAX)
        if not title:
            continue
        url = clean_text(raw.get("link"), URL_MAX)
        url = url if url.startswith("https://") and " " not in url else None
        try:
            stamp = datetime.fromtimestamp(
                int(raw["providerPublishTime"]), timezone.utc
            )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            stamp = now
        tickers = []
        for value in raw.get("relatedTickers") or ():
            try:
                tickers.append(canonical_symbol(clean_text(value, 8)))
            except ValueError:
                continue
        item = NewsItem(
            title=title,
            publisher=clean_text(raw.get("publisher"), PUBLISHER_MAX) or "unknown",
            url=url,
            published_at=min(stamp, now),
            related_tickers=tuple(dict.fromkeys(tickers))[:MAX_TICKERS],
        )
        if (item.url or item.title) not in seen:
            seen.add(item.url or item.title)
            items.append(item)
    return tuple(items)


def infer_sentiment(title):
    text = title.lower()
    if any(word in text for word in _POSITIVE):
        return "positive"
    if any(word in text for word in _NEGATIVE):
        return "negative"
    return "neutral"


def impact_score(sentiment, tickers, published_at, now):
    age_hours = max((now - published_at).total_seconds() / 3600, 0.0)
    recency = 1.0 if age_hours <= 24 else 0.8 if age_hours <= 72 else 0.55
    return round(
        recency
        * (1.0 if sentiment != "neutral" else 0.7)
        * (1.0 + min(len(tickers), 4) * 0.08),
        4,
    )


def dedupe_stories(stories):
    best = {}
    for story in stories:
        key = story.url or story.title.lower()
        if key not in best or story.impact_score > best[key].impact_score:
            best[key] = story
    return list(best.values())


def match_topic(query):
    words = set(query.lower().split())
    scored = [(len(words & set(t.keywords)), t) for t in TOPICS]
    best = max(scored, key=lambda pair: pair[0])
    return best[1] if best[0] else None


def link_stocks(topic, sentiment, related):
    bias = {"positive": "up", "negative": "down"}.get(sentiment, "mixed")
    links = {
        s: LinkedStock(
            symbol=s, bias=bias, reason="Ticker named by the news feed for this story."
        )
        for s in related
    }
    if topic is not None and sentiment != "neutral":
        up, down = (
            (topic.bullish_if_positive, topic.bearish_if_positive)
            if sentiment == "positive"
            else (topic.bullish_if_negative, topic.bearish_if_negative)
        )
        for group, side in ((up, "up"), (down, "down")):
            for symbol in group:
                links.setdefault(
                    symbol,
                    LinkedStock(
                        symbol=symbol,
                        bias=side,
                        reason=f"Topic mapping '{topic.label}' for {sentiment} sentiment.",
                    ),
                )
    return tuple(links.values())[:MAX_TICKERS]


def build_story(item, topic, now):
    sentiment = infer_sentiment(item.title)
    return Story(
        story_id=hashlib.sha256((item.url or item.title).encode()).hexdigest()[:12],
        title=item.title,
        publisher=item.publisher,
        url=item.url,
        published_at=item.published_at,
        topic_slug=topic.slug if topic else "search",
        sentiment=sentiment,
        directional_bias={"positive": "up", "negative": "down"}.get(sentiment, "mixed"),
        impact_score=impact_score(
            sentiment, item.related_tickers, item.published_at, now
        ),
        related_tickers=item.related_tickers,
        linked_stocks=link_stocks(topic, sentiment, item.related_tickers),
    )


def separate_conflicts(bullish_ranked, bearish_ranked, limit):
    """Symbols with evidence on both sides become disagreements, never a netted score."""
    bull = {i.symbol: i for i in bullish_ranked}
    bear = {i.symbol: i for i in bearish_ranked}
    disagreements = []
    for symbol in sorted(
        (s for s in bull if s in bear), key=lambda s: -(bull[s].score + bear[s].score)
    ):
        up, down = bull[symbol], bear[symbol]
        side = (
            "balanced"
            if up.score == down.score
            else "up"
            if up.score > down.score
            else "down"
        )
        disagreements.append(
            Disagreement(
                symbol=symbol,
                up_score=up.score,
                down_score=down.score,
                up_story_ids=up.story_ids,
                down_story_ids=down.story_ids,
                stronger_side=side,
            )
        )
    return (
        tuple(i for i in bullish_ranked if i.symbol not in bear)[:limit],
        tuple(i for i in bearish_ranked if i.symbol not in bull)[:limit],
        tuple(disagreements[:limit]),
    )


def _rank(stories, side):
    scores, ids = {}, {}
    for story in stories:
        for link in story.linked_stocks:
            if link.bias == side:
                scores[link.symbol] = scores.get(link.symbol, 0.0) + story.impact_score
                ids.setdefault(link.symbol, []).append(story.story_id)
    return [
        StockLean(symbol=s, score=round(v, 4), story_ids=tuple(ids[s]))
        for s, v in sorted(scores.items(), key=lambda kv: -kv[1])
    ]


class NewsService:
    def __init__(self, providers, deadline_seconds=DEFAULT_DEADLINE_SECONDS):
        self.providers = providers
        self.deadline = deadline_seconds

    async def search_news(self, query, limit=10) -> ProviderResult[tuple[Story, ...]]:
        limit = min(max(int(limit), 1), 20)
        now = self.providers.clock()
        result = await self.providers.news(query, limit)
        if result.value is None:
            return ProviderResult[tuple[Story, ...]](
                status=result.status, issues=result.issues
            )
        topic = match_topic(query)
        stories = dedupe_stories(build_story(i, topic, now) for i in result.value)
        stories.sort(key=lambda s: -s.impact_score)
        return ProviderResult[tuple[Story, ...]](
            status="completed", value=tuple(stories[:limit])
        )

    async def build_today_report(self, limit=5) -> ProviderResult[NewsReport]:
        limit = min(max(int(limit), 1), 20)
        now = self.providers.clock()
        tasks = [asyncio.ensure_future(self.providers.news(t.query, 6)) for t in TOPICS]
        _, pending = await asyncio.wait(tasks, timeout=self.deadline)
        for task in pending:
            task.cancel()
        stories, issues, failed = [], [], []
        for topic, task in zip(TOPICS, tasks):
            if task in pending or task.cancelled() or task.exception() is not None:
                failed.append(topic.slug)
                issues.append(
                    ProviderIssue(
                        source_id="yahoo-news",
                        code="timeout",
                        message=f"News topic '{topic.slug}' did not complete.",
                    )
                )
                continue
            result = task.result()
            if result.value is None:
                failed.append(topic.slug)
                issues.extend(result.issues)
                continue
            stories.extend(build_story(i, topic, now) for i in result.value)
        if len(failed) == len(TOPICS):
            return ProviderResult[NewsReport](
                status="unavailable", issues=tuple(issues[:32])
            )
        stories = sorted(dedupe_stories(stories), key=lambda s: -s.impact_score)
        bullish, bearish, disagreements = separate_conflicts(
            _rank(stories, "up"), _rank(stories, "down"), limit
        )
        report = NewsReport(
            as_of=now,
            stories=tuple(stories[:50]),
            bullish=bullish,
            bearish=bearish,
            disagreements=disagreements,
            topics_failed=tuple(failed),
        )
        if failed:
            return ProviderResult[NewsReport](
                status="partial", value=report, issues=tuple(dict.fromkeys(issues))[:32]
            )
        return ProviderResult[NewsReport](status="completed", value=report)
