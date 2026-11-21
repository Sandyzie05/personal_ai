from pathlib import Path

from scripts.state_report import main


def test_quick_report_is_redacted_and_complete():
    report = main(quick=True)
    assert "## Code" in report and "oMLX" in report and "<ROWS>" in report
    assert str(Path.home()) not in report
    assert "Bearer" not in report


def test_forecast_keys_accept_conventional_env_names(monkeypatch):
    import importlib
    import src.config as config

    for name in ("PERSONAL_AI_FRED_API_KEY", "PERSONAL_AI_SEC_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FRED_API_KEY", "synthetic-fred")
    monkeypatch.setenv("SEC_USER_AGENT", "synthetic-agent")
    try:
        importlib.reload(config)
        assert config.FORECAST_FRED_API_KEY == "synthetic-fred"
        assert config.FORECAST_SEC_USER_AGENT == "synthetic-agent"
        monkeypatch.setenv("PERSONAL_AI_FRED_API_KEY", "prefixed-wins")
        importlib.reload(config)
        assert config.FORECAST_FRED_API_KEY == "prefixed-wins"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
