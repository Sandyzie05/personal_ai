"""Fixed public adapters selected from stock_predictor.real_data_fetcher.

No synthetic quotes/news or static company fallbacks. Historical prices alone
do not establish point-in-time research availability.
"""

from __future__ import annotations

import asyncio
import re
import json
from datetime import date, datetime, timezone
from urllib.parse import quote

import httpx
from pydantic import Field, SecretStr, ValidationError

from .. import config
from .cache import TTLCache
from .models import (
    Contract,
    EvidenceCard,
    MarketData,
    PriceBar,
    ProviderIssue,
    ProviderResult,
    ResearchRequest,
    SourceId,
    canonical_symbol,
)


class ForecastSettings(Contract):
    http_timeout_seconds: float = Field(
        default=config.FORECAST_HTTP_TIMEOUT_SECONDS, gt=0, le=30
    )
    http_max_bytes: int = Field(
        default=config.FORECAST_HTTP_MAX_BYTES, ge=1024, le=8_000_000
    )
    research_deadline_seconds: float = Field(
        default=config.FORECAST_RESEARCH_DEADLINE_SECONDS, gt=0, le=120
    )
    scenario_deadline_seconds: float = Field(
        default=config.FORECAST_SCENARIO_DEADLINE_SECONDS, gt=0, le=300
    )
    model_call_budget: int = Field(
        default=config.FORECAST_MODEL_CALL_BUDGET, ge=4, le=8
    )
    model_max_tokens: int = Field(
        default=config.FORECAST_MODEL_MAX_TOKENS, ge=128, le=1024
    )
    snapshot_max_chars: int = Field(
        default=config.FORECAST_SNAPSHOT_MAX_CHARS, ge=1024, le=24000
    )
    model_output_max_chars: int = Field(
        default=config.FORECAST_MODEL_OUTPUT_MAX_CHARS, ge=256, le=8000
    )
    sec_user_agent: str = Field(default=config.FORECAST_SEC_USER_AGENT, max_length=200)
    fred_api_key: SecretStr = SecretStr(config.FORECAST_FRED_API_KEY)
    alpha_vantage_api_key: SecretStr = SecretStr(config.FORECAST_ALPHA_VANTAGE_API_KEY)

    @property
    def api_configured(self):
        return bool(self.alpha_vantage_api_key.get_secret_value())


class ProviderFailure(Exception):
    def __init__(self, source_id: SourceId, code, message):
        self.issue = ProviderIssue(source_id=source_id, code=code, message=message)
        super().__init__(message)


def unavailable(source, code, message, status="unavailable"):
    return ProviderResult(
        status=status,
        issues=(ProviderIssue(source_id=source, code=code, message=message),),
    )


class PublicDataProviders:
    """Inject ``transport`` for offline tests; no caller-supplied URL operation."""

    def __init__(self, settings=None, transport=None, clock=None):
        self.settings = settings or ForecastSettings()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = TTLCache()
        self._sec_lock = asyncio.Lock()
        self._sec_last = 0.0

    async def _json(self, source, path="", *, params=None):
        # Internal routing is deliberately narrower than arbitrary URL fetching.
        routes = {
            "yahoo-chart": (
                "https://query1.finance.yahoo.com/v8/finance/chart/",
                "market",
            ),
            "alpha-vantage": ("https://www.alphavantage.co/query", "alpha"),
            "fred": ("https://api.stlouisfed.org/fred/series/observations", "fred"),
            "sec-edgar": ("https://data.sec.gov/submissions/", "sec"),
            "yahoo-search": (
                "https://query1.finance.yahoo.com/v1/finance/search",
                "news",
            ),
        }
        base, kind = routes[source]
        # SourceId is a closed Literal owned by models.py; news shares the Yahoo family id.
        issue_source = "yahoo-news" if source == "yahoo-search" else source
        if kind == "news":
            query = " ".join(str(path).split())
            if not 1 <= len(query) <= 80 or not query.isprintable():
                raise ValueError("unsupported news query")
            count = min(max(int((params or {}).get("newsCount", 8)), 1), 20)
            params = {"q": query, "newsCount": count, "quotesCount": 0, "listsCount": 0}
            url = base
        elif kind == "market":
            symbol = canonical_symbol(path)
            url = base + quote(symbol.replace(".", "-"), safe="")
        elif kind == "sec":
            if path == "tickers":
                url = "https://www.sec.gov/files/company_tickers.json"
            elif len(path) == 10 and path.isdigit():
                url = base + "CIK" + path + ".json"
            else:
                raise ValueError("unsupported SEC identifier")
        elif path:
            raise ValueError("unsupported provider path")
        else:
            url = base
        if source == "sec-edgar":
            async with self._sec_lock:
                now = asyncio.get_running_loop().time()
                await asyncio.sleep(max(0, 0.2 - (now - self._sec_last)))
                self._sec_last = asyncio.get_running_loop().time()
        headers = {"Accept": "application/json"}
        if source == "sec-edgar":
            headers["User-Agent"] = self.settings.sec_user_agent
        for attempt in range(2):
            try:
                if self.transport is not None:
                    return await asyncio.wait_for(
                        self.transport(source, url, dict(params or {}), headers),
                        self.settings.http_timeout_seconds,
                    )
                async with httpx.AsyncClient(
                    timeout=self.settings.http_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "GET", url, params=params, headers=headers
                    ) as response:
                        if response.status_code != 200:
                            code = (
                                "transport"
                                if response.status_code in {429, 502, 503, 504}
                                else "http"
                            )
                            raise ProviderFailure(
                                issue_source,
                                code,
                                "Public provider returned non-success status.",
                            )
                        if "json" not in response.headers.get("content-type", ""):
                            raise ProviderFailure(
                                issue_source,
                                "schema",
                                "Public provider did not return JSON.",
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.settings.http_max_bytes:
                                raise ProviderFailure(
                                    issue_source,
                                    "budget",
                                    "Public response exceeds size bound.",
                                )
                        return json.loads(body)
            except ProviderFailure as exc:
                if attempt == 0 and exc.issue.code == "transport":
                    continue
                raise
            except (TimeoutError, httpx.TimeoutException):
                if attempt == 0:
                    continue
                raise ProviderFailure(
                    issue_source, "timeout", "Public provider request timed out."
                ) from None
            except (ValueError, UnicodeError):
                raise ProviderFailure(
                    issue_source, "schema", "Invalid public JSON response."
                ) from None
            except (httpx.TransportError, OSError):
                if attempt == 0:
                    continue
                raise ProviderFailure(
                    issue_source, "transport", "Public provider transport unavailable."
                ) from None
            except Exception:
                raise ProviderFailure(
                    issue_source, "unavailable", "Public provider adapter failed."
                ) from None

    async def market(self, request: ResearchRequest) -> ProviderResult[MarketData]:
        request = ResearchRequest.model_validate(request)
        if request.as_of is not None:
            return unavailable(
                "yahoo-chart",
                "unsupported_as_of",
                "Historical point-in-time research is unsupported.",
                "unsupported",
            )
        now = self.clock()
        cache_key = (
            "market",
            request.symbol,
            now.date(),
            request.sessions,
            tuple(s for s in request.sessions if s.closes_at <= now),
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        issues = []
        sources = ["yahoo-chart"]
        if self.settings.api_configured:
            sources.append("alpha-vantage")
        for source in sources:
            try:
                if source == "yahoo-chart":
                    data = await self._json(
                        source,
                        request.symbol,
                        params={"range": "3mo", "interval": "1d"},
                    )
                    result = self._parse_yahoo(request.symbol, data, request.sessions)
                else:
                    data = await self._json(
                        source,
                        params={
                            "function": "TIME_SERIES_DAILY_ADJUSTED",
                            "symbol": request.symbol,
                            "outputsize": "compact",
                            "apikey": self.settings.alpha_vantage_api_key.get_secret_value(),
                        },
                    )
                    overview = await self._json(
                        source,
                        params={
                            "function": "OVERVIEW",
                            "symbol": request.symbol,
                            "apikey": self.settings.alpha_vantage_api_key.get_secret_value(),
                        },
                    )
                    result = self._parse_alpha(
                        request.symbol, data, overview, request.sessions
                    )
                value = ProviderResult[MarketData](
                    status="partial" if issues else "completed",
                    value=result,
                    issues=tuple(issues),
                )
                return self.cache.set(cache_key, value)
            except ProviderFailure as exc:
                issues.append(exc.issue)
            except (
                KeyError,
                TypeError,
                ValueError,
                IndexError,
                ValidationError,
                OverflowError,
            ):
                issues.append(
                    ProviderIssue(
                        source_id=source,
                        code="schema",
                        message="Invalid or incomplete market response.",
                    )
                )
        return ProviderResult[MarketData](status="unavailable", issues=tuple(issues))

    @staticmethod
    def _completed_session(day, exchange, sessions, now):
        from zoneinfo import ZoneInfo

        today = now.astimezone(ZoneInfo("America/New_York")).date()
        if sessions and sessions[0].exchange != exchange:
            raise ValueError("completion schedule exchange mismatch")
        if day > today:
            return False
        supplied = next((item for item in sessions if item.session == day), None)
        if supplied is not None:
            return supplied.closes_at <= now
        # Without a caller-attested close, today's candle is not known final.
        return day < today

    def _parse_yahoo(self, symbol, payload, sessions=()):
        (result,) = payload["chart"]["result"]
        meta = result["meta"]
        if (
            canonical_symbol(meta["symbol"].replace("-", ".")) != symbol
            or meta["currency"] != "USD"
        ):
            raise ValueError("market identity mismatch")
        exchange = {
            "NYQ": "XNYS",
            "NMS": "XNAS",
            "NGM": "XNAS",
            "NCM": "XNAS",
            "NYSE": "XNYS",
            "NASDAQ": "XNAS",
        }.get(meta["exchangeName"])
        if not exchange:
            raise ValueError("unsupported market exchange")
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("America/New_York")
        now = self.clock()
        closes = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]
        adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
        quote = result["indicators"]["quote"][0]

        def aligned(name):  # optional arrays: a misaligned one is dropped, never guessed
            arr = quote.get(name)
            return arr if isinstance(arr, list) and len(arr) == len(closes) else [None] * len(closes)

        highs, lows, volumes = aligned("high"), aligned("low"), aligned("volume")
        if len(timestamps) != len(closes) or (
            adjusted is not None and len(adjusted) != len(closes)
        ):
            raise ValueError("misaligned price arrays")
        bars = []
        for index, stamp in enumerate(timestamps):
            observed = datetime.fromtimestamp(stamp, timezone.utc)
            if observed > now or not self._completed_session(
                observed.astimezone(zone).date(),
                exchange,
                sessions,
                now,
            ):
                continue
            if closes[index] is None:
                continue
            bars.append(
                PriceBar(
                    symbol=symbol,
                    session=observed.astimezone(zone).date(),
                    close=closes[index],
                    adjusted_close=adjusted[index] if adjusted else None,
                    high=highs[index] or None,
                    low=lows[index] or None,
                    volume=volumes[index],
                    source_id="yahoo-chart",
                    retrieved_at=now,
                )
            )
        if not bars:
            raise ValueError("no completed daily bars")
        bars = sorted(bars, key=lambda b: b.session)[-252:]
        card = EvidenceCard(
            source_id="yahoo-chart",
            source_type="market",
            symbol=symbol,
            title="Completed daily market prices",
            summary=(
                f"{len(bars)} daily closes; latest session {bars[-1].session}. "
                "Current-session completion requires a caller-attested exchange close."
            ),
            retrieved_at=now,
            confidence=0.8,
        )
        return MarketData(
            symbol=symbol,
            exchange=exchange,
            currency="USD",
            bars=tuple(bars),
            price_convention=(
                "split_dividend_adjusted_close"
                if all(b.adjusted_close is not None for b in bars)
                else "unadjusted_close"
            ),
            evidence=(card,),
        )

    def _parse_alpha(self, symbol, payload, overview, sessions=()):
        if (
            canonical_symbol(overview["Symbol"]) != symbol
            or canonical_symbol(payload["Meta Data"]["2. Symbol"]) != symbol
            or overview["Currency"] != "USD"
        ):
            raise ValueError("Alpha identity mismatch")
        exchange = {"NASDAQ": "XNAS", "NYSE": "XNYS"}.get(overview["Exchange"])
        if not exchange:
            raise ValueError("Alpha exchange identity unavailable")
        now = self.clock()
        bars = tuple(
            PriceBar(
                symbol=symbol,
                session=date.fromisoformat(day),
                close=row["4. close"],
                adjusted_close=row["5. adjusted close"],
                high=row.get("2. high"),
                low=row.get("3. low"),
                volume=row.get("6. volume"),
                source_id="alpha-vantage",
                retrieved_at=now,
            )
            for day, row in sorted(payload["Time Series (Daily)"].items())[-252:]
            if self._completed_session(date.fromisoformat(day), exchange, sessions, now)
        )
        if not bars:
            raise ValueError("Alpha no completed bars")
        evidence = EvidenceCard(
            source_id="alpha-vantage",
            source_type="market",
            symbol=symbol,
            title="Alpha Vantage completed daily prices",
            summary=f"{len(bars)} adjusted daily closes; exchange verified by provider overview.",
            retrieved_at=now,
            confidence=0.8,
        )
        return MarketData(
            symbol=symbol,
            exchange=exchange,
            currency="USD",
            bars=bars,
            price_convention="split_dividend_adjusted_close",
            evidence=(evidence,),
        )

    async def macro(self, request):
        from .macro import MacroSignalService

        return await MacroSignalService(self).get_macro_snapshot(
            ResearchRequest.model_validate(request)
        )

    async def filings(self, request):
        from .filings import FilingSignalService

        return await FilingSignalService(self).get_filing_snapshot(
            ResearchRequest.model_validate(request)
        )

    async def news(self, query, limit=8):
        from .news import NewsItem, parse_news

        # Public egress: a user-typed query must not carry personal identifiers (emails, long digit runs).
        if re.search(r"@|\d[\d .-]{5,}\d|\b\d{3}-\d{2}-\d{4}\b", str(query)):
            return unavailable("yahoo-news", "schema", "News query looks like it contains personal identifiers; not sent.")
        try:
            data = await self._json("yahoo-search", query, params={"newsCount": limit})
            items = parse_news(data, self.clock())
        except ValueError:
            return unavailable(
                "yahoo-news", "schema", "Unsupported news query or response."
            )
        except ProviderFailure as exc:
            return ProviderResult[tuple[NewsItem, ...]](
                status="unavailable", issues=(exc.issue,)
            )
        except (KeyError, TypeError, IndexError, AttributeError, ValidationError):
            return unavailable(
                "yahoo-news", "schema", "Invalid or incomplete news response."
            )
        return ProviderResult[tuple[NewsItem, ...]](status="completed", value=items)
