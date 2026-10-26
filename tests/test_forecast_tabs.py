"""Offline Forecast sections tests: fake transports, throwaway vault, no network."""

from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json

import pytest
from streamlit.testing.v1 import AppTest

from src.forecaster.models import TradingSession
from src.forecaster.outcomes import SessionPrice, create_forecast, evaluate_outcome
from src.forecaster.providers import ForecastSettings, PublicDataProviders
from test_forecast_page import MemoryVault, SESSIONS, prediction

_spec = importlib.util.spec_from_file_location(
    "forecast_page_tabs_under_test", Path(__file__).parents[1] / "src/interface/forecast.py"
)
forecast_page = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forecast_page)

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
START = int(datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc).timestamp())


def chart(symbol, closes):
    return {"chart": {"result": [{
        "meta": {"symbol": symbol, "currency": "USD", "exchangeName": "NMS"},
        "timestamp": [START + 86400 * i for i in range(len(closes))],
        "indicators": {"quote": [{"close": closes}]},
    }]}}


def fake_providers(fail=(), news=None, calls=None):
    async def transport(source, url, params, headers):
        if calls is not None:
            calls.append(source)
        if source == "yahoo-search":
            if news is None:
                raise OSError("down")
            return {"news": news}
        symbol = url.rsplit("/", 1)[1]
        if symbol in fail or "*" in fail:
            raise OSError("down")
        return chart(symbol, [100 + i for i in range(60)])

    return PublicDataProviders(ForecastSettings(http_timeout_seconds=10), transport, lambda: NOW)


def _test_page():
    import streamlit as st

    st.session_state["forecast_page_renderer"](st.session_state["synthetic_vault"])


def _launch(tmp_path, monkeypatch, providers, vault=None):
    import dotenv

    home = tmp_path / "synthetic-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(forecast_page._tabs_module(), "_providers", lambda: providers)
    at = AppTest.from_file(str(Path(__file__).resolve()), default_timeout=60)
    at.session_state["synthetic_vault"] = vault or MemoryVault()
    at.session_state["forecast_page_renderer"] = forecast_page.render_forecast_page
    at.run()
    assert not at.exception
    return at


def goto(at, section):
    at.segmented_control[0].set_value(section).run()
    assert not at.exception


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


def texts(at):
    return " ".join(
        el.value for group in (at.warning, at.info, at.caption, at.text, at.markdown, at.error)
        for el in group
    )


def seeded_vault():
    vault = MemoryVault()
    sessions = tuple(TradingSession.model_validate(s) for s in SESSIONS)
    pred = prediction(sessions)
    fc = create_forecast(pred, 5, sessions=sessions)
    vault.records[forecast_page.SNAPSHOT_PREFIX + "a"] = {"forecast": fc.to_api()}
    prices = tuple(
        SessionPrice(symbol=sym, exchange=ex, session=f"2026-10-{d}", close=c,
                     source_id="yahoo-chart", retrieved_at=f"2026-10-{d}T21:00:00Z")
        for sym, ex, d, c in (("AAPL", "XNAS", 19, 100), ("AAPL", "XNAS", 26, 110),
                              ("SPY", "ARCX", 19, 200), ("SPY", "ARCX", 26, 204))
    )
    out = evaluate_outcome(fc, prices=prices,
                           evaluated_at=datetime(2026, 10, 27, 21, tzinfo=timezone.utc))
    vault.records[forecast_page.OUTCOME_PREFIX + "a"] = {
        "forecast_id": fc.forecast_id, "outcome": out.to_api()}
    return vault


def test_opening_each_section_makes_no_network_calls(tmp_path, monkeypatch):
    calls = []
    at = _launch(tmp_path, monkeypatch, fake_providers(calls=calls))
    for section in ("Recommendations", "Validation", "Events", "Drilldown & System", "Screens"):
        goto(at, section)
    assert calls == []
    assert at.session_state["synthetic_vault"].writes == []


def test_recommendations_labelled_research_lean_with_partial_failure(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers(fail={"MSFT"}))
    goto(at, "Recommendations")
    at.text_input[0].set_value("AAPL, MSFT, bad ticker")
    click(at, "Compute research leans")
    body = texts(at)
    assert "Research lean" in body and "not a buy/sell" in body
    assert "MSFT: market data unavailable" in body
    assert "Rejected" in body
    assert at.table and "research lean" in at.table[0].value.columns
    assert "buy" not in " ".join(str(c) for c in at.table[0].value.columns).lower()


def test_recommendations_all_unavailable_is_explicit(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers(fail={"*"}))
    goto(at, "Recommendations")
    click(at, "Compute research leans")
    assert "No symbol passed" in texts(at)
    assert "market data unavailable" in texts(at)


def test_validation_empty_state_and_csv_download(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers())
    goto(at, "Validation")
    assert "nothing to validate" in texts(at)
    assert not at.download_button

    at = _launch(tmp_path, monkeypatch, fake_providers(), vault=seeded_vault())
    goto(at, "Validation")
    assert at.download_button and at.download_button[0].label == "Download validation CSV"
    assert any("Directional accuracy: insufficient sample" in m.value for m in at.markdown)


def test_validation_flags_unreadable_records(tmp_path, monkeypatch):
    vault = seeded_vault()
    vault.records[forecast_page.SNAPSHOT_PREFIX + "broken"] = {"forecast": {"x": 1}}
    at = _launch(tmp_path, monkeypatch, fake_providers(), vault=vault)
    goto(at, "Validation")
    assert any("could not be read" in w.value for w in at.warning)


def test_events_unavailable_and_untrusted_rendering(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers(news=None))
    goto(at, "Events")
    click(at, "Fetch today's stories")
    assert any("unavailable" in w.value for w in at.warning)

    headline = "<script>alert(1)</script> **bold** AAPL beats"
    item = {"title": headline, "link": "https://example.com/a", "publisher": "<b>Wire</b>",
            "providerPublishTime": int(NOW.timestamp()) - 60, "relatedTickers": ["AAPL"]}
    at = _launch(tmp_path, monkeypatch, fake_providers(news=[item]))
    goto(at, "Events")
    click(at, "Fetch today's stories")
    shown = [t.value for t in at.text]
    assert any("alert(1)" in s for s in shown)
    assert not any("alert(1)" in m.value for m in at.markdown)


def test_drilldown_and_system_health(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers())
    goto(at, "Drilldown & System")
    assert "No provider calls yet" in texts(at)
    at.text_input[0].set_value("AAPL")
    click(at, "Load technicals")
    body = texts(at)
    assert "Unavailable: atr" in body and "Research lean" in body
    assert any("healthy" in str(t.value.to_dict()) for t in at.table)

    at = _launch(tmp_path, monkeypatch, fake_providers(fail={"*"}))
    goto(at, "Drilldown & System")
    at.text_input[0].set_value("AAPL")
    click(at, "Load technicals")
    assert any("unavailable" in w.value for w in at.warning)


def test_screens_run_and_unavailable(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, fake_providers())
    goto(at, "Screens")
    click(at, "Run screen")
    assert "Analyzed 50 of 50" in texts(at)
    assert at.table

    at = _launch(tmp_path, monkeypatch, fake_providers(fail={"*"}))
    goto(at, "Screens")
    click(at, "Run screen")
    assert any("unavailable" in w.value for w in at.warning)
    assert not at.table


if __name__ == "__main__":
    _test_page()
