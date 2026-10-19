import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.forecaster.filings import FilingSignalService
from src.forecaster.macro import MacroSignalService
from src.forecaster.models import (
    EvidenceCard, MarketData, PriceBar, ProviderResult, ResearchRequest, TradingSession,
)
from src.forecaster.outcomes import create_forecast
from src.forecaster.providers import ForecastSettings, PublicDataProviders, unavailable
from src.forecaster.research import ResearchPredictionService

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def market():
    evidence = EvidenceCard(
        source_id="yahoo-chart", source_type="market", symbol="NVDA",
        title="Prices", summary="Completed closes", retrieved_at=NOW, confidence=.8)
    return MarketData(
        symbol="NVDA", exchange="XNAS", currency="USD", price_convention="unadjusted_close",
        bars=tuple(PriceBar(symbol="NVDA", session=date(2026, 10, 1) + timedelta(days=i),
                            close=100 + i, source_id="yahoo-chart", retrieved_at=NOW)
                   for i in range(10)), evidence=(evidence,))


class Providers:
    settings = ForecastSettings()

    async def market(self, request):
        return ProviderResult(status="completed", value=market())

    async def macro(self, request):
        return unavailable("fred", "not_configured", "No FRED key.")

    async def filings(self, request):
        return unavailable("sec-edgar", "not_configured", "No SEC contact.")


def test_real_market_features_with_partial_context_are_not_probabilities():
    result = asyncio.run(ResearchPredictionService(Providers()).research(ResearchRequest(symbol="NVDA")))
    assert result.status == "partial"
    assert [h.horizon_sessions for h in result.horizons] == [5, 21]
    assert all(h.score_kind == "uncalibrated_heuristic" for h in result.horizons)
    assert all(h.coverage == pytest.approx(1 / 3) for h in result.horizons)
    assert all(.42 <= h.heuristic_score <= .58 for h in result.horizons)
    assert result.source_provenance[0].source_id == "yahoo-chart"
    assert result.snapshot.historical_supported is False


def test_market_absence_abstains_instead_of_theme_only_forecast():
    class Missing(Providers):
        async def market(self, request):
            return unavailable("yahoo-chart", "unavailable", "No prices.")
    result = asyncio.run(ResearchPredictionService(Missing()).research(ResearchRequest(symbol="NVDA")))
    assert result.status == "abstained"
    assert not result.horizons


def test_historical_request_never_calls_latest_providers():
    class Forbidden:
        settings = ForecastSettings()

        def __getattr__(self, name):
            pytest.fail("unsupported historical research must not call providers")
    result = asyncio.run(ResearchPredictionService(Forbidden()).research(
        ResearchRequest(symbol="NVDA", as_of=NOW)))
    assert result.status == "unsupported"
    assert not result.horizons


def test_original_macro_thresholds_and_cpi_level_limit():
    score, notes = MacroSignalService.summarize({
        "FEDFUNDS": 5, "T10Y2Y": -.5, "UNRATE": 6,
        "BAMLH0A0HYM2": 5, "DGS10": 5, "CPIAUCSL": 300,
    })
    assert score == pytest.approx(-.8)
    assert any("not an inflation rate" in note for note in notes)


def test_macro_partial_observations_and_latest_vintage_label():
    async def transport(source, url, params, headers):
        return {"observations": [{"date": "2026-10-01",
                                  "value": "5" if params["series_id"] == "FEDFUNDS" else "."}]}
    provider = PublicDataProviders(
        settings=ForecastSettings(fred_api_key="test-only"), transport=transport, clock=lambda: NOW)
    result = asyncio.run(provider.macro(ResearchRequest(symbol="NVDA")))
    assert result.status == "partial"
    assert result.value.score == -.2
    assert len(result.issues) == 5
    assert result.value.evidence[0].point_in_time is False


def test_sec_recency_not_cumulative_quarterly_growth_and_alignment_rejected():
    payload = {"tickers": ["NVDA"], "filings": {"recent": {
        "form": ["10-Q"], "filingDate": ["2026-10-01"], "accessionNumber": ["test"],
    }}}
    result = FilingSignalService.build_signal("NVDA", payload, NOW)
    assert result.value.score == .08
    assert "cumulative" in result.value.notes[0]
    payload["filings"]["recent"]["form"].append("10-K")
    with pytest.raises(ValueError):
        FilingSignalService.build_signal("NVDA", payload, NOW)


def test_research_deadline_preserves_completed_peers():
    class Slow(Providers):
        settings = ForecastSettings(research_deadline_seconds=.02)

        async def macro(self, request):
            await asyncio.sleep(1)
    result = asyncio.run(ResearchPredictionService(Slow()).research(ResearchRequest(symbol="NVDA")))
    assert result.status == "partial"
    assert result.snapshot.market is not None
    assert any(issue.code == "budget" for issue in result.snapshot.issues)


def test_full_injected_provider_pipeline_uses_real_parsers_and_requested_horizon():
    calls = []

    async def transport(source, url, params, headers):
        calls.append((source, url))
        if source == "yahoo-chart":
            timestamps = [int(datetime(2026, 10, day, 14, tzinfo=timezone.utc).timestamp())
                          for day in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]
            return {"chart": {"result": [{
                "meta": {"symbol": "NVDA", "currency": "USD", "exchangeName": "NMS"},
                "timestamp": timestamps,
                "indicators": {"quote": [{"close": list(range(100, 110))}]},
            }]}}
        if source == "fred":
            return {"observations": [{"value": "3", "date": "2026-10-01"}]}
        assert headers["User-Agent"] == "Offline Forecast Tests test@example.invalid"
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "NVDA", "cik_str": 1045810}}
        assert url.endswith("/CIK0001045810.json")
        return {"tickers": ["NVDA"], "filings": {"recent": {
            "form": ["10-Q"], "filingDate": ["2026-10-01"], "accessionNumber": ["test"],
        }}}

    providers = PublicDataProviders(
        settings=ForecastSettings(
            fred_api_key="test-only", sec_user_agent="Offline Forecast Tests test@example.invalid"),
        transport=transport, clock=lambda: NOW,
    )
    result = asyncio.run(ResearchPredictionService(providers).research(
        ResearchRequest(symbol="NVDA", horizons=(5,))))
    assert result.status == "completed"
    assert len(result.horizons) == 1
    assert result.horizons[0].horizon_sessions == 5
    assert result.horizons[0].coverage == 1
    assert {source.source_id for source in result.source_provenance} == {
        "yahoo-chart", "fred", "sec-edgar", "curated-ai-infrastructure-theme",
    }
    assert len(calls) == 9


@pytest.mark.parametrize("source", ["yahoo-chart", "alpha-vantage"])
@pytest.mark.parametrize("day,close_hour,prior,future", [
    (date(2026, 10, 19), 16, (12, 13, 14, 15, 16),
     (date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22),
      date(2026, 10, 23), date(2026, 10, 26))),
    (date(2026, 11, 27), 13, (19, 20, 23, 24, 25),
     (date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 2),
      date(2026, 12, 3), date(2026, 12, 4))),
])
def test_real_provider_research_to_forecast_respects_attested_close_and_cache(
    source, day, close_hour, prior, future,
):
    zone = ZoneInfo("America/New_York")
    days = tuple(day.replace(day=value) for value in prior) + (day,)
    sessions = tuple(TradingSession(
        exchange="XNAS", session=session,
        closes_at=datetime(session.year, session.month, session.day,
                           close_hour if session == day else 16, tzinfo=zone),
    ) for session in (*days, *future))
    request = ResearchRequest(symbol="NVDA", horizons=(5,), sessions=sessions)
    clock = [sessions[len(prior)].closes_at - timedelta(minutes=1)]
    calls = []

    async def transport(provider, url, params, headers):
        calls.append(provider)
        if provider == "yahoo-chart":
            if source == "alpha-vantage":
                return {}
            return {"chart": {"result": [{
                "meta": {"symbol": "NVDA", "currency": "USD", "exchangeName": "NMS"},
                "timestamp": [int(datetime(session.year, session.month, session.day,
                                          9, 30, tzinfo=zone).timestamp()) for session in days],
                "indicators": {"quote": [{"close": list(range(100, 100 + len(days)))}]},
            }]}}
        if params["function"] == "OVERVIEW":
            return {"Symbol": "NVDA", "Currency": "USD", "Exchange": "NASDAQ"}
        return {"Meta Data": {"2. Symbol": "NVDA"}, "Time Series (Daily)": {
            session.isoformat(): {"4. close": str(100 + index), "5. adjusted close": str(100 + index)}
            for index, session in enumerate(days)}}

    providers = PublicDataProviders(
        settings=ForecastSettings(alpha_vantage_api_key="test-only" if source == "alpha-vantage" else ""),
        transport=transport, clock=lambda: clock[0],
    )

    def research():
        with patch("src.forecaster.research.datetime") as research_clock:
            research_clock.now.return_value = clock[0].astimezone(timezone.utc)
            return asyncio.run(ResearchPredictionService(providers).research(request))

    before = research()
    assert before.snapshot.market.bars[-1].session == days[-2]
    assert create_forecast(before, 5, sessions=sessions).origin.session == days[-2]
    before_calls = len(calls)
    clock[0] = datetime(day.year, day.month, day.day, close_hour + 2, tzinfo=zone)
    after = research()
    assert len(calls) > before_calls  # a pre-close cached result cannot hide the new close
    assert after.snapshot.market.bars[-1].session == day
    assert after.snapshot.market.bars[-1].source_id == source
    forecast = create_forecast(after, 5, sessions=sessions)
    assert forecast.origin.session == day
    assert forecast.target.session == future[-1]
    assert forecast.session_sequence == after.snapshot.sessions
    with pytest.raises(ValueError, match="attestation"):
        create_forecast(after, 5, sessions=sessions[:-1])
