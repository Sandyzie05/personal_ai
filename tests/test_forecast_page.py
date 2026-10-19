"""Offline synthetic Forecast UI tests; never open the user's vault or .env."""

from copy import deepcopy
from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from src.data_vault import DataVaultError
from src.forecaster.models import (
    DataSourceProfile, EvidenceCard, EvidenceSnapshot, HorizonPrediction,
    MarketData, PriceBar, ResearchPrediction,
)
# Load the real page without importing unrelated Agents/MCP package initialization.
_spec = importlib.util.spec_from_file_location(
    "forecast_page_under_test", Path(__file__).parents[1] / "src/interface/forecast.py"
)
forecast_page = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forecast_page)
OUTCOME_PREFIX = forecast_page.OUTCOME_PREFIX
SNAPSHOT_PREFIX = forecast_page.SNAPSHOT_PREFIX
_scenario_factory = forecast_page._scenario_factory
save_rendered_snapshot = forecast_page.save_rendered_snapshot
snapshot_digest = forecast_page.snapshot_digest

NOW = datetime(2026, 10, 19, 21, tzinfo=timezone.utc)
SESSIONS = [
    {"exchange": "XNAS", "session": f"2026-10-{day}",
     "closes_at": f"2026-10-{day}T20:00:00Z"}
    for day in (19, 20, 21, 22, 23, 26)
]


class MemoryVault:
    def __init__(self):
        self.records = {}
        self.writes = []

    def retrieve_data(self, key):
        return deepcopy(self.records.get(key))

    def list_keys(self):
        return list(self.records)

    def store_immutable_data(self, key, payload):
        previous = self.records.get(key)
        if previous is not None and previous != payload:
            raise DataVaultError("Immutable record already has different content")
        self.writes.append(key)
        self.records[key] = deepcopy(payload)
        return previous is None


def prediction(sessions=()):
    evidence = EvidenceCard(
        source_id="yahoo-chart", source_type="market", title="Synthetic close",
        summary="Synthetic public data", retrieved_at=NOW, confidence=1, symbol="AAPL",
    )
    market = MarketData(
        symbol="AAPL", exchange="XNAS", currency="USD",
        bars=(PriceBar(symbol="AAPL", session=date(2026, 10, 19), close=100,
                       source_id="yahoo-chart", retrieved_at=NOW),),
        price_convention="unadjusted_close", evidence=(evidence,),
    )
    return ResearchPrediction(
        snapshot=EvidenceSnapshot(symbol="AAPL", as_of=NOW, created_at=NOW,
                                  market=market, evidence=(evidence,), issues=(), sessions=sessions),
        status="partial",
        horizons=(HorizonPrediction(
            horizon_sessions=5, heuristic_score=.5, downside_score=.5, coverage=.5,
            outlook="mixed", expected_excess_return_pct=1,
            reasoning=("Synthetic partial coverage",),
        ),),
        events=(), theme_exposures=(),
        source_provenance=(DataSourceProfile(
            source_id="yahoo-chart", name="Synthetic source", categories=("market",),
            caveats="Synthetic offline fixture; not point-in-time data",
        ),),
        limitations=("Partial source coverage",),
    )


def _test_page():
    import streamlit as st

    st.session_state["forecast_page_renderer"](st.session_state["synthetic_vault"])


@pytest.fixture
def page(tmp_path, monkeypatch):
    import dotenv
    from src.forecaster.research import ResearchPredictionService

    home = tmp_path / "synthetic-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    calls = []

    async def research(self, request):
        calls.append(request)
        return prediction(request.sessions)

    monkeypatch.setattr(ResearchPredictionService, "research", research)
    at = AppTest.from_file(str(Path(__file__).resolve()), default_timeout=30)
    at.session_state["synthetic_vault"] = MemoryVault()
    at.session_state["forecast_page_renderer"] = forecast_page.render_forecast_page
    at.run()
    assert not at.exception
    return at, calls


def click(at, label):
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception


def research_page(page, sessions=SESSIONS):
    at, calls = page
    at.text_input[0].set_value("aapl")
    if sessions:
        next(item for item in at.text_area if item.label.startswith("Official")).set_value(
            json.dumps(sessions)
        )
        at.checkbox[0].check()
    click(at, "Research ticker")
    assert calls[0].symbol == "AAPL"
    return at


def prepare(page):
    at = research_page(page)
    click(at, "Prepare forecast")
    assert "Save forecast" in [button.label for button in at.button]
    return at


def test_opening_page_never_researches_or_saves(page):
    at, calls = page
    assert calls == []
    assert at.session_state["synthetic_vault"].writes == []
    assert "Save forecast" not in [button.label for button in at.button]


def test_invalid_ticker_never_reaches_public_provider(page):
    at, calls = page
    at.text_input[0].set_value("private account text")
    click(at, "Research ticker")
    assert calls == []
    assert at.error


def test_research_shows_provenance_partial_status_and_no_automatic_save(page):
    at = research_page(page)
    assert any("partial" in item.value for item in at.subheader)
    assert any("Provider provenance" in item.value for item in at.subheader)
    assert any("Uncalibrated" in item.value for item in at.warning)
    assert at.session_state["synthetic_vault"].writes == []
    assert "Save forecast" not in [button.label for button in at.button]


def test_forecast_needs_confirmed_schedule_then_exact_explicit_save(page):
    at = prepare(page)
    vault = at.session_state["synthetic_vault"]
    assert vault.writes == []
    click(at, "Save forecast")
    keys = [key for key in vault.list_keys() if key.startswith(SNAPSHOT_PREFIX)]
    assert len(keys) == 1
    saved = vault.retrieve_data(keys[0])
    assert saved["forecast_id"] and saved["snapshot_id"] and saved["model_id"]
    assert saved["research"]["source_provenance"]
    assert saved["session_sequence"] == SESSIONS
    click(at, "Save forecast")
    assert len(vault.list_keys()) == 1


def test_unconfirmed_schedule_cannot_prepare(page):
    at, calls = page
    at.text_input[0].set_value("AAPL")
    next(item for item in at.text_area if item.label.startswith("Official")).set_value(
        json.dumps(SESSIONS)
    )
    click(at, "Research ticker")
    assert at.error
    assert calls == []
    assert not at.session_state["synthetic_vault"].writes
    assert "Save forecast" not in [button.label for button in at.button]


def test_incomplete_target_schedule_cannot_be_saved(page):
    at = research_page(page, sessions=SESSIONS[:2])
    click(at, "Prepare forecast")
    assert at.error
    assert "Save forecast" not in [button.label for button in at.button]
    assert not at.session_state["synthetic_vault"].writes


def test_specialists_run_only_by_explicit_action_with_public_snapshot(page, monkeypatch):
    from src.forecaster.scenarios import ScenarioSwarmService

    seen = []

    async def analyze(self, snapshot):
        seen.append(snapshot)
        return SimpleNamespace(to_api=lambda: {"status": "partial", "coverage": .5})

    monkeypatch.setattr(ScenarioSwarmService, "analyze", analyze)
    at = research_page(page)
    vault = at.session_state["synthetic_vault"]
    vault.records["private_document"] = {"text": "never forwarded"}
    assert seen == []
    click(at, "Run local specialists")
    assert seen[0].snapshot_id == ResearchPrediction.model_validate(
        at.session_state["forecast_research_result"]
    ).snapshot.snapshot_id
    assert "never forwarded" not in seen[0].model_dump_json()
    assert not vault.writes


def test_mutated_preview_loses_save_approval(page):
    at = prepare(page)
    payload, digest = at.session_state["forecast_prepared"]
    payload["model_id"] = "mutated"
    at.session_state["forecast_prepared"] = (payload, digest)
    click(at, "Save forecast")
    assert not at.session_state["synthetic_vault"].writes
    assert any("changed" in item.value for item in at.error)


def test_outcome_evaluation_requires_separate_save_and_keeps_missing_data(page):
    at = prepare(page)
    click(at, "Save forecast")
    at.run()
    vault = at.session_state["synthetic_vault"]
    writes_before = len(vault.writes)
    next(item for item in at.text_area if item.label.startswith("Session closing")).set_value("[]")
    next(item for item in at.text_input if item.label.startswith("Evaluation timestamp")).set_value(
        "2026-10-27T21:00:00Z"
    )
    click(at, "Evaluate outcome")
    assert len(vault.writes) == writes_before
    assert "missing" in json.dumps(at.session_state["forecast_evaluation"][0]).lower()
    click(at, "Save outcome")
    assert any(key.startswith(OUTCOME_PREFIX) for key in vault.list_keys())
    writes_before = len(vault.writes)
    at.run()
    assert len(vault.writes) == writes_before


def test_exact_symbol_and_benchmark_session_prices_produce_visible_saved_outcome(page):
    at = prepare(page)
    click(at, "Save forecast")
    at.run()
    prices = [
        {"symbol": symbol, "exchange": exchange, "session": f"2026-10-{day}",
         "close": close, "source_id": "yahoo-chart",
         "retrieved_at": f"2026-10-{day}T21:00:00Z"}
        for symbol, exchange, day, close in (
            ("AAPL", "XNAS", 19, 100), ("AAPL", "XNAS", 26, 110),
            ("SPY", "ARCX", 19, 200), ("SPY", "ARCX", 26, 204),
        )
    ]
    next(item for item in at.text_area if item.label.startswith("Session closing")).set_value(
        json.dumps(prices)
    )
    next(item for item in at.text_input if item.label.startswith("Evaluation timestamp")).set_value(
        "2026-10-27T21:00:00Z"
    )
    click(at, "Evaluate outcome")
    outcome = at.session_state["forecast_evaluation"][0]["outcome"]
    assert outcome["status"] == "evaluated"
    assert outcome["realized_excess_return_pct"] == pytest.approx(8)
    vault = at.session_state["synthetic_vault"]
    assert not any(key.startswith(OUTCOME_PREFIX) for key in vault.list_keys())
    click(at, "Save outcome")
    saved = next(vault.retrieve_data(key) for key in vault.list_keys() if key.startswith(OUTCOME_PREFIX))
    assert saved["outcome"] == outcome
    assert len(saved["prices"]) == 4


def test_save_rejects_mutation_or_wrong_namespace_without_writing():
    vault = MemoryVault()
    payload = {"record_id": "synthetic", "forecast_id": "test"}
    approved = snapshot_digest(payload)
    payload["forecast_id"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        save_rendered_snapshot(vault, SNAPSHOT_PREFIX, payload, approved)
    with pytest.raises(ValueError, match="namespace"):
        save_rendered_snapshot(vault, "document_", payload, snapshot_digest(payload))
    assert vault.writes == []


def test_specialists_refuse_remote_settings_before_building_client(monkeypatch):
    from src.ai_engine import engine

    vault = MemoryVault()
    vault.records["ollama_config"] = {
        "engine": "omlx", "llm_host": "https://remote.invalid", "llm_model": "synthetic",
    }
    monkeypatch.setattr(engine, "build_chat_client", lambda **kwargs: pytest.fail("remote client"))
    with pytest.raises(ValueError, match="loopback"):
        _scenario_factory(vault)


def test_specialists_reuse_saved_factory_settings_without_vault_context(monkeypatch):
    from src.ai_engine import engine

    vault = MemoryVault()
    vault.records["ollama_config"] = {
        "engine": "omlx", "llm_host": "http://127.0.0.1:8000",
        "llm_model": "synthetic-local", "context_window_tokens": 4096,
    }
    vault.records["private_document"] = {"text": "never forwarded"}
    seen = []
    monkeypatch.setattr(engine, "build_chat_client", lambda **kwargs: seen.append(kwargs))
    _scenario_factory(vault)(remaining_seconds=.25)
    assert seen[0]["model"] == "synthetic-local"
    assert seen[0]["context_window_tokens"] == 4096
    assert seen[0]["request_timeout_seconds"] == .25
    assert seen[0]["safe_local_transport"] is True
    assert "never forwarded" not in json.dumps(seen)


def test_unscheduled_research_cannot_prepare_outcome_tracking(page):
    at = research_page(page, sessions=())
    assert "Prepare forecast" not in [button.label for button in at.button]
    assert any("unscheduled" in item.value.lower() for item in at.caption)
    assert not at.session_state["synthetic_vault"].writes


def test_unsent_schedule_edits_do_not_change_research_or_prepared_snapshot(page):
    at = research_page(page)
    next(item for item in at.text_area if item.label.startswith("Official")).set_value(
        json.dumps(SESSIONS[:2])
    )
    click(at, "Prepare forecast")
    payload = at.session_state["forecast_prepared"][0]
    assert payload["session_sequence"] == SESSIONS
    assert payload["research"]["snapshot"]["sessions"] == SESSIONS


def test_main_forecast_navigation_stays_behind_login_gate(tmp_path, monkeypatch):
    import dotenv
    import src.data_vault
    from src.security.auth import AuthManager

    auth_api = pytest.importorskip("mcp.shared.auth", reason="Full app requires compatible MCP SDK")
    if not hasattr(auth_api, "AuthorizationCodeResult"):
        pytest.skip("Installed MCP SDK lacks AuthorizationCodeResult; full navigation blocked")

    home = tmp_path / "navigation-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(AuthManager, "is_registered", lambda self: True)
    fake = MemoryVault()
    monkeypatch.setattr(src.data_vault, "create_vault", lambda **kwargs: fake)
    main = Path(__file__).parents[1] / "src/interface/main.py"
    at = AppTest.from_file(str(main), default_timeout=30)
    at.run()
    assert not at.exception
    assert not at.radio
    at.session_state["vault_key"] = b"s" * 32
    app = at.session_state["app"]
    monkeypatch.setattr(app, "render_chat_page", lambda: None)
    at.run()
    assert not at.exception
    at.radio[0].set_value("forecast").run()
    assert not at.exception
    assert any(item.value == "Forecast" for item in at.header)
    assert app.chat_engine is None
    assert fake.writes == []
    assert not (home / ".personal_ai_auth").exists()
    assert not (home / ".personal_ai_vault").exists()


if __name__ == "__main__":
    _test_page()
