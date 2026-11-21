from test_forecast_tabs import _launch, click, goto, texts
from test_trader_engines import day_bars, synthetic_history


class FakeProviders:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def intraday(self, symbol):
        self.calls += 1
        if self.fail:
            raise OSError("down")
        hist = [b for d in synthetic_history(12).values() for b in d]
        return hist + day_bars("2026-02-02", [100.0] * 3 + [100.05, 101.0] + [101.0] * 6)


def test_trader_section_no_network_until_click_and_renders_plan(tmp_path, monkeypatch):
    fake = FakeProviders()
    at = _launch(tmp_path, monkeypatch, fake)
    goto(at, "Trader")
    assert fake.calls == 0 and "not financial advice" in texts(at)
    at.text_input(key="trader_symbol").set_value("tsla")
    click(at, "Plan trade")
    body = texts(at)
    heads = " ".join(h.value for h in at.subheader).lower()
    assert fake.calls == 1 and "triggered" in heads and "not a prediction" in heads
    assert [m.label for m in at.metric] == ["Entry", "Stop", "Target", "Shares"]
    assert not any(w in body.lower() for w in ("place order", "buy now", "ticket"))


def test_trader_bad_ticker_and_provider_failure(tmp_path, monkeypatch):
    at = _launch(tmp_path, monkeypatch, FakeProviders(fail=True))
    goto(at, "Trader")
    at.text_input(key="trader_symbol").set_value("not a ticker")
    click(at, "Plan trade")
    assert any("US ticker" in e.value for e in at.error)
    at.text_input(key="trader_symbol").set_value("TSLA")
    click(at, "Plan trade")
    assert any("unavailable" in e.value for e in at.error)
