import asyncio
from datetime import datetime, timezone

import pytest

from src.forecaster.cache import TTLCache
from src.forecaster.models import ResearchRequest
from src.forecaster.providers import ForecastSettings, PublicDataProviders

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def yahoo():
    return {"chart": {"result": [{
        "meta": {"symbol": "NVDA", "currency": "USD", "exchangeName": "NMS"},
        "timestamp": [1792157400, 1792416600],
        "indicators": {"quote": [{"close": [100, 101]}],
                       "adjclose": [{"adjclose": [99, 100]}]},
    }]}}


def test_market_canonical_query_identity_and_cache():
    calls = []

    async def transport(source, url, params, headers):
        calls.append((source, url, params))
        return yahoo()

    providers = PublicDataProviders(transport=transport, clock=lambda: NOW)
    result = asyncio.run(providers.market(ResearchRequest(symbol="nvda")))
    assert result.status == "completed"
    assert result.value.exchange == "XNAS"
    assert result.value.bars[0].adjusted_close == 99
    assert len(result.value.bars) == 1  # future/incomplete session excluded
    assert result.value.evidence[0].source_id == "yahoo-chart"
    assert asyncio.run(providers.market(ResearchRequest(symbol="NVDA"))) == result
    assert len(calls) == 1
    assert calls[0][1].endswith("/NVDA")


@pytest.mark.parametrize("mutate", [
    lambda p: p["chart"]["result"][0]["meta"].update(symbol="AMD"),
    lambda p: p["chart"]["result"][0]["meta"].update(currency="EUR"),
    lambda p: p["chart"]["result"][0]["indicators"]["quote"][0].update(close=[float("nan")]),
])
def test_malformed_market_never_becomes_synthetic_success(mutate):
    async def transport(*args):
        data = yahoo()
        mutate(data)
        return data

    result = asyncio.run(PublicDataProviders(transport=transport, clock=lambda: NOW).market(
        ResearchRequest(symbol="NVDA")))
    assert result.status == "unavailable"
    assert result.value is None
    assert result.issues[0].code == "schema"


def test_historical_claim_rejected_before_transport():
    async def transport(*args):
        pytest.fail("historical unsupported request must not use live endpoint")

    result = asyncio.run(PublicDataProviders(transport=transport).market(
        ResearchRequest(symbol="NVDA", as_of=NOW)))
    assert result.status == "unsupported"


def test_timeout_retries_once_and_reports_typed_failure():
    calls = []

    async def transport(*args):
        calls.append(1)
        raise TimeoutError()

    result = asyncio.run(PublicDataProviders(
        settings=ForecastSettings(), transport=transport,
    ).market(ResearchRequest(symbol="NVDA")))
    assert len(calls) == 2
    assert result.issues[0].code == "timeout"


def test_alpha_fallback_preserves_actual_provider_and_failed_primary():
    calls = []

    async def transport(source, url, params, headers):
        calls.append((source, params))
        if source == "yahoo-chart":
            return {"chart": {"result": []}}
        if params["function"] == "OVERVIEW":
            return {"Symbol": "NVDA", "Currency": "USD", "Exchange": "NASDAQ"}
        return {"Meta Data": {"2. Symbol": "NVDA"}, "Time Series (Daily)": {
            "2026-10-16": {"4. close": "100", "5. adjusted close": "99"},
        }}

    result = asyncio.run(PublicDataProviders(
        settings=ForecastSettings(alpha_vantage_api_key="test-only"),
        transport=transport, clock=lambda: NOW,
    ).market(ResearchRequest(symbol="NVDA")))
    assert result.status == "partial"
    assert result.value.bars[0].source_id == "alpha-vantage"
    assert result.value.evidence[0].source_id == "alpha-vantage"
    assert result.value.exchange == "XNAS"
    assert result.issues[0].source_id == "yahoo-chart"
    assert len(calls) == 3


def test_alpha_cannot_invent_missing_exchange():
    async def transport(source, url, params, headers):
        if source == "yahoo-chart":
            return {}
        if params["function"] == "OVERVIEW":
            return {"Symbol": "NVDA", "Currency": "USD", "Exchange": "UNKNOWN"}
        return {"Meta Data": {"2. Symbol": "NVDA"}, "Time Series (Daily)": {
            "2026-10-16": {"4. close": "100", "5. adjusted close": "99"},
        }}
    result = asyncio.run(PublicDataProviders(
        settings=ForecastSettings(alpha_vantage_api_key="test-only"),
        transport=transport, clock=lambda: NOW,
    ).market(ResearchRequest(symbol="NVDA")))
    assert result.status == "unavailable"
    assert result.issues[-1].source_id == "alpha-vantage"


@pytest.mark.parametrize("response,expected", [
    ((302, {"location": "https://unapproved.invalid"}, b""), "http"),
    ((200, {"content-type": "text/html"}, b"not JSON"), "schema"),
    ((200, {"content-type": "application/json"}, b"x" * 2048), "budget"),
])
def test_native_http_boundary_rejects_redirect_type_and_size(monkeypatch, response, expected):
    import httpx
    original = httpx.AsyncClient
    status, headers, body = response
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=body)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(PublicDataProviders(
        settings=ForecastSettings(http_max_bytes=1024),
    ).market(ResearchRequest(symbol="NVDA")))
    assert result.status == "unavailable"
    assert result.issues[0].code == expected
    assert len(requests) == 1
    assert requests[0].url.host == "query1.finance.yahoo.com"


def test_cache_monotonic_expiry_and_capacity():
    now = [0]
    cache = TTLCache(max_entries=2, ttl_seconds=2, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.set("c", 3)
    assert cache.get("b") is None
    now[0] = 2
    assert cache.get("a") is None
    assert cache.get("c") is None
