"""Track R UI: Robinhood features degrade visibly, import tickers only, and keep account figures out of the vault."""

import json
from datetime import date

from evals.robinhood_fixtures import (
    CANARY_ACCOUNT,
    CANARY_QTY,
    FakeRobinhoodMcp,
    responses,
)
from src.forecaster.robinhood import RobinhoodClient
from test_forecast_page import click, forecast_page, page, research_page  # noqa: F401 (page is a fixture)
from test_forecast_tabs import _launch, fake_providers, goto, texts


def _rh(monkeypatch, symbol="AAPL"):
    mcp = FakeRobinhoodMcp(responses(date.today(), symbol=symbol))
    monkeypatch.setattr(
        forecast_page._tabs_module(),
        "robinhood_client",
        lambda vault: RobinhoodClient(mcp, "rh"),
    )
    return mcp


def test_robinhood_features_degrade_visibly_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(
        forecast_page._tabs_module(), "robinhood_client", lambda vault: None
    )
    at = _launch(tmp_path, monkeypatch, fake_providers())
    for section in ("Watchlist", "Events", "Screens"):
        goto(at, section)
        assert "Robinhood not connected" in texts(at)


def test_watchlist_and_screens_import_tickers_and_events_show_earnings(
    tmp_path, monkeypatch
):
    mcp = _rh(monkeypatch)
    at = _launch(tmp_path, monkeypatch, fake_providers())
    goto(at, "Watchlist")
    click(at, "Load my Robinhood watchlists and scans")
    click(at, "Import tickers")
    assert at.text_input[0].value == "NVDA, AMD"
    goto(at, "Screens")
    click(at, "Load my Robinhood watchlists and scans")
    next(s for s in at.selectbox if s.label == "Import from").set_value(
        1
    ).run()  # the saved scan
    click(at, "Import tickers")
    assert any(t.value == "AVGO" for t in at.text_input)
    goto(at, "Events")
    click(at, "Load earnings calendar")
    assert any(
        "symbol" in df.value.columns and "MSFT" in df.value["symbol"].tolist()
        for df in at.dataframe
    )
    assert not {name for name, _ in mcp.calls} & {
        "get_accounts",
        "get_equity_positions",
        "get_portfolio",
    }


def test_portfolio_fit_is_explicit_private_and_never_saved(page, monkeypatch):
    at = research_page(page)
    assert any("Robinhood not connected" in c.value for c in at.caption)
    mcp = _rh(monkeypatch)
    at.run()
    assert not any(
        name == "get_equity_positions" for name, _ in mcp.calls
    )  # nothing read until the click
    click(at, "Load my position (read-only)")
    table = next(df.value for df in at.dataframe if "item" in df.value.columns)
    assert "yes" in table["value"].tolist()

    class Local:
        temperature, max_tokens, model = 0.0, None, "m"

        def chat(self, **kw):
            assert "tools" not in kw
            return {
                "message": {
                    "content": json.dumps(
                        {"summary": "Held position; one of 2 holdings."}
                    )
                }
            }

    monkeypatch.setattr(
        forecast_page,
        "_scenario_factory",
        lambda vault: (lambda remaining_seconds=60: Local()),
    )
    click(at, "Explain with AI (local)")
    assert any("one of 2 holdings" in t.value for t in at.text)
    stored = json.dumps(at.session_state["synthetic_vault"].records, default=str)
    assert (
        CANARY_ACCOUNT not in stored and str(CANARY_QTY) not in stored
    )  # activity log: ids/status only


def test_outcome_closes_fill_from_robinhood(page, monkeypatch):
    at = research_page(page)
    click(at, "Prepare forecast")
    click(at, "Save forecast")
    _rh(monkeypatch)
    at.run()
    click(at, "Fill closes from Robinhood")
    filled = next(
        t for t in at.text_area if t.label.startswith("Session closing prices")
    ).value
    rows = json.loads(filled) if filled else []
    assert rows and all(r["source_id"] == "robinhood" for r in rows)
    assert {r["symbol"] for r in rows} == {"AAPL", "SPY"}
    assert not any("could not" in e.value for e in at.error)
