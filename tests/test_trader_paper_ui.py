from test_forecast_tabs import _launch, click, goto, texts
from test_trader_paper import Providers, factory


def _open(tmp_path, monkeypatch):
    from test_forecast_tabs import forecast_page

    at = _launch(tmp_path, monkeypatch, Providers())
    monkeypatch.setattr(forecast_page, "_scenario_factory", lambda v: factory())
    goto(at, "Trader")
    at.segmented_control(key="trader_view").set_value("Paper agents").run()
    assert not at.exception
    return at


def test_paper_agents_backtest_renders_scoreboard(tmp_path, monkeypatch):
    at = _open(tmp_path, monkeypatch)
    assert "Paper money only" in texts(at) and "No paper trades yet" in texts(at)
    at.text_input(key="paper_symbols").set_value("AAA").run()
    click(at, "Run backtest (past year)")
    assert any("new AI decisions" in s.value for s in at.success)
    heads = [h.value for h in at.subheader]
    assert {"Leaderboard", "P&L by month", "Trade log"} <= set(heads)
    board = at.dataframe[0].value
    assert set(board["account"]) == {"agent", "baseline"}
    click(at, "Review agents")
    assert "Reviewer (checked against the numbers)" in [h.value for h in at.subheader]


def test_paper_agents_rejects_bad_tickers(tmp_path, monkeypatch):
    at = _open(tmp_path, monkeypatch)
    at.text_input(key="paper_symbols").set_value("not a ticker").run()
    click(at, "Run backtest (past year)")
    assert any("US tickers" in e.value for e in at.error)
