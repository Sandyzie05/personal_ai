import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from evals.robinhood_fixtures import (
    CANARY_ACCOUNT,
    HOSTILE,
    FakeRobinhoodMcp,
    responses,
)
from src.forecaster import robinhood as rh
from src.forecaster.models import (
    EvidenceCard,
    MarketData,
    PriceBar,
    ProviderResult,
    ResearchRequest,
)
from src.forecaster.providers import ForecastSettings, unavailable
from src.forecaster.research import ResearchPredictionService
from src.forecaster.tot import _drivers

NOW = datetime.now(timezone.utc)


def client(symbol="NVDA", hostile=False, fail=(), **kw):
    mcp = FakeRobinhoodMcp(
        responses(NOW.date(), symbol=symbol, hostile=hostile, **kw), fail=fail
    )
    return rh.RobinhoodClient(mcp, "rh"), mcp


class Providers:
    settings = ForecastSettings()

    async def market(self, request):
        ev = EvidenceCard(
            source_id="yahoo-chart",
            source_type="market",
            symbol="NVDA",
            title="Prices",
            summary="Completed closes",
            retrieved_at=NOW,
            confidence=0.8,
        )
        bars = tuple(
            PriceBar(
                symbol="NVDA",
                session=NOW.date() - timedelta(days=20 - i),
                close=100 + i,
                source_id="yahoo-chart",
                retrieved_at=NOW,
            )
            for i in range(10)
        )
        return ProviderResult(
            status="completed",
            value=MarketData(
                symbol="NVDA",
                exchange="XNAS",
                currency="USD",
                price_convention="unadjusted_close",
                bars=bars,
                evidence=(ev,),
            ),
        )

    async def macro(self, request):
        return unavailable("fred", "not_configured", "No FRED key.")

    async def filings(self, request):
        return unavailable("sec-edgar", "not_configured", "No SEC contact.")


@pytest.mark.parametrize(
    "tool",
    [
        "get_bank_accounts",
        "get_banking_transactions",
        "get_credit_transactions",
        "place_equity_order",
        "create_alert",
        "decline_trade_approval",
        "set_legend_chart_settings",
        "add_to_watchlist",
        "get_pnl_trade_history",
    ],
)
def test_allowlist_refuses_banking_write_and_unlisted_tools(tool):
    c, mcp = client()
    with pytest.raises(PermissionError):
        c.call(tool, {})
    assert mcp.calls == []


def test_argument_guard_blocks_free_text_and_account_number_outside_account_tools():
    c, mcp = client()
    for args in (
        {"symbols": ["NVDA"], "note": "hi"},
        {"symbols": ["not a ticker"]},
        {"symbol": HOSTILE},
        {"symbols": ["NVDA"], "account_number": CANARY_ACCOUNT},
    ):
        with pytest.raises(PermissionError):
            c.call("get_equity_fundamentals", args)
    assert mcp.calls == []
    with pytest.raises(ConnectionError) as err:  # server error text is never echoed
        rh.RobinhoodClient(FakeRobinhoodMcp({}, fail={"get_equity_quotes"}), "rh").call(
            "get_equity_quotes", {"symbols": ["NVDA"]}
        )
    assert "evil" not in str(err.value)


def test_research_cards_are_numbers_only_and_hostile_broker_text_is_dropped():
    c, mcp = client(hostile=True)
    result = rh.research_evidence(c, "NVDA", NOW)
    assert result.status == "completed"
    kinds = sorted(card.source_type for card in result.value)
    assert kinds == [
        "earnings",
        "filing",
        "fundamental",
        "fundamental",
        "fundamental",
        "market",
        "options",
    ]
    assert all(card.source_id == "robinhood" for card in result.value)
    text = json.dumps([card.to_api() for card in result.value])
    assert "Ignore previous" not in text and "evil.test" not in text
    assert {
        name for name, _ in mcp.calls
    } <= rh.ROBINHOOD_RESEARCH_TOOLS  # research never touches the account


def test_one_family_failing_keeps_the_rest_and_not_configured_is_visible():
    c, _ = client(fail={"get_equity_fundamentals", "get_option_chains"})
    result = rh.research_evidence(c, "NVDA", NOW)
    assert result.status == "partial" and len(result.value) == 5
    assert {i.message for i in result.issues} >= {
        "Robinhood fundamentals unavailable.",
        "Robinhood implied_move unavailable.",
    }
    missing = rh.research_evidence(None, "NVDA", NOW)
    assert (
        missing.status == "unavailable" and missing.issues[0].code == "not_configured"
    )


def test_research_snapshot_gains_robinhood_families_without_moving_the_heuristic():
    request = ResearchRequest(symbol="NVDA")
    plain = asyncio.run(ResearchPredictionService(Providers()).research(request))
    c, mcp = client()
    rich = asyncio.run(
        ResearchPredictionService(Providers(), robinhood=c).research(request)
    )
    assert [h.heuristic_score for h in rich.horizons] == [
        h.heuristic_score for h in plain.horizons
    ]
    assert "robinhood" in {p.source_id for p in rich.source_provenance}
    drivers = {d[0] for d in _drivers(rich.snapshot)}
    assert {"valuation", "earnings", "options"} <= drivers
    packet = json.dumps([card.to_api() for card in rich.snapshot.evidence])
    assert (
        len(packet) < ForecastSettings().snapshot_max_chars
    )  # still fits the critic budget
    assert not {name for name, _ in mcp.calls} & rh.ROBINHOOD_ACCOUNT_TOOLS


def test_ratings_and_financials_set_sentiment_from_numbers():
    c, _ = client(target_avg=300.0, price=230.0, revenue_growth=-0.2)
    cards = {
        card.title.split(" ", 1)[1]: card
        for card in rh.research_evidence(c, "NVDA", NOW).value
    }
    assert cards["analyst ratings (Robinhood)"].sentiment == "positive"
    assert cards["reported financials (Robinhood)"].sentiment == "negative"


def test_portfolio_fit_and_local_explainer():
    c, mcp = client()
    fit = rh.portfolio_fit(c, "NVDA", 230.0)
    assert (
        fit.held
        and fit.positions == 2
        and fit.trust == "private"
        and 0 < fit.weight_pct <= 100
    )
    assert all(
        name in rh.ROBINHOOD_ACCOUNT_TOOLS
        for name, args in mcp.calls
        if "account_number" in args
    )

    class Local:
        temperature, max_tokens, model = 0.0, None, "m"

        def __init__(self, text):
            self.text = text

        def chat(self, **kw):
            assert "tools" not in kw
            return {"message": {"content": json.dumps({"summary": self.text})}}

    note, status, _ = rh.explain_fit(
        Local("Held; about 2 positions in total."), {}, fit
    )
    assert status == "completed" and note.summary
    _, status, _ = rh.explain_fit(Local("You should sell some."), {}, fit)
    assert status == "invalid"


def test_imports_are_tickers_only_and_closes_skip_interpolated_bars():
    c, _ = client()
    assert [k for k, _, _ in rh.list_sources(c)] == ["watchlist", "scan"]
    assert rh.import_symbols(c, "watchlist", "wl-1") == [
        "NVDA",
        "AMD",
    ]  # crypto pair dropped
    assert rh.import_symbols(c, "scan", "scan-1") == ["AVGO"]
    closes = rh.session_closes(
        c, ["NVDA", "SPY"], NOW.date() - timedelta(days=10), NOW.date()
    )
    assert set(closes) == {"NVDA", "SPY"} and all(v for v in closes.values())
    cal = rh.earnings_calendar(c, NOW.date())
    assert cal[0].symbol == "MSFT" and cal[0].timing == "pm"


def test_r8_evals_pass_and_catch_planted_leaks(monkeypatch):
    import logging

    from evals import robinhood as ev

    assert ev.run_broker_injection()["violations"] == 0
    assert ev.run_account_egress()["violations"] == 0
    assert ev.run_valuation_golden() == {"passed": 3, "total": 3}
    real = rh.fundamentals_card
    monkeypatch.setattr(
        rh,
        "fundamentals_card",
        lambda *a: real(*a).model_copy(update={"summary": HOSTILE}),
    )
    assert (
        ev.run_broker_injection()["violations"] > 0
    )  # eval is sensitive to free text reaching a card
    monkeypatch.setattr(rh, "fundamentals_card", real)
    leaky = rh.research_evidence

    def research_evidence(*a, **k):
        logging.getLogger("leak").warning("acct %s", CANARY_ACCOUNT)
        return leaky(*a, **k)

    monkeypatch.setattr(rh, "research_evidence", research_evidence)
    assert (
        ev.run_account_egress()["violations"] > 0
    )  # eval is sensitive to a canary in a log line
