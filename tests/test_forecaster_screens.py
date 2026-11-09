import asyncio
from datetime import datetime, timezone

import pytest

from src.forecaster.providers import ForecastSettings, PublicDataProviders
from src.forecaster.screens import SP500_TOP50, ScreenService

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
START = int(datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc).timestamp())


def chart(symbol, closes):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "currency": "USD",
                        "exchangeName": "NMS",
                    },
                    "timestamp": [START + 86400 * i for i in range(len(closes))],
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def providers(series, fail=(), slow=()):
    async def transport(source, url, params, headers):
        symbol = url.rsplit("/", 1)[1]
        if symbol in fail:
            raise OSError("down")
        if symbol in slow:
            await asyncio.sleep(5)
        return chart(symbol, series[symbol])

    return PublicDataProviders(
        ForecastSettings(http_timeout_seconds=10), transport, lambda: NOW
    )


UP = [100 + i for i in range(60)]
DOWN = [160 - i for i in range(60)]
FLAT_HIGH = [100.0] * 59 + [99.5]
SERIES = {"UPP": UP, "DWN": DOWN, "FLT": FLAT_HIGH}


def run(svc, kind, symbols=None, **kw):
    return asyncio.run(svc.screen(kind, symbols or list(SERIES), **kw))


def test_period_high_labels_window_and_ranks_by_proximity():
    result = run(ScreenService(providers(SERIES)), "period_high")
    assert result.status == "completed"
    items = result.value.items
    assert [i.symbol for i in items] == ["UPP", "FLT"]  # DWN is far below its high
    assert items[0].pct_below_high == 0 and items[0].rank == 1
    assert result.value.window.startswith("period high/low")
    assert "not financial advice" in result.value.note


def test_period_low():
    result = run(ScreenService(providers(SERIES)), "period_low")
    assert [i.symbol for i in result.value.items][
        0
    ] == "DWN"  # FLT also sits at its low


def test_momentum_strong_buy_and_sell_are_opposite():
    svc = ScreenService(providers(SERIES))
    assert [i.symbol for i in run(svc, "strong_buy").value.items] == ["UPP"]
    assert [i.symbol for i in run(svc, "strong_sell").value.items][0] == "DWN"


def test_price_stretch_screens():
    series = {"STR": [100.0] * 59 + [130.0], "CHP": [100.0] * 59 + [70.0]}
    svc = ScreenService(providers(series))
    assert [i.symbol for i in run(svc, "overvalued", list(series)).value.items] == [
        "STR"
    ]
    assert [i.symbol for i in run(svc, "undervalued", list(series)).value.items] == [
        "CHP"
    ]


def test_outage_for_every_symbol_is_explicitly_unavailable():
    result = run(ScreenService(providers(SERIES, fail=set(SERIES))), "period_high")
    assert result.status == "unavailable" and result.value is None
    assert result.issues


def test_partial_when_some_symbols_fail_or_are_rejected():
    result = run(
        ScreenService(providers(SERIES, fail={"FLT"})),
        "period_high",
        ["UPP", "FLT", "not a ticker"],
    )
    assert result.status == "partial"
    assert result.value.symbols_failed == ("FLT",)
    assert result.value.symbols_rejected == ("not a ti",)
    assert result.issues


def test_deadline_flags_slow_symbols_as_failed():
    svc = ScreenService(providers(SERIES, slow={"FLT"}), deadline_seconds=0.3)
    result = run(svc, "period_high")
    assert result.status == "partial" and result.value.symbols_failed == ("FLT",)
    assert [i.symbol for i in result.value.items] == ["UPP"]


def test_concurrency_bound_respected():
    live = peak = 0

    async def transport(source, url, params, headers):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return chart(url.rsplit("/", 1)[1], UP)

    prov = PublicDataProviders(ForecastSettings(), transport, lambda: NOW)
    asyncio.run(
        ScreenService(prov, concurrency=2).screen("period_high", list(SP500_TOP50[:12]))
    )
    assert peak <= 2


def test_bundled_universe_and_input_validation():
    svc = ScreenService(providers(SERIES))
    assert len(SP500_TOP50) == 50 and len(set(SP500_TOP50)) == 50
    assert asyncio.run(svc.sp500_list(5)) == SP500_TOP50[:5]
    with pytest.raises(ValueError):
        asyncio.run(svc.screen("bogus", ["UPP"]))
    with pytest.raises(ValueError):
        ScreenService(providers(SERIES), concurrency=0)
