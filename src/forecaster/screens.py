"""Price-only stock screens, adapted from stock_predictor.stock_lists.

Windows are bounded by the market provider's range (about 3 months of daily
closes), so "high/low" means PERIOD high/low, never all-time. Valuation screens
are price-stretch proxies (no fundamentals are fetched). Rankings are research
leans, not orders.
"""

from __future__ import annotations

import asyncio
from datetime import date
from statistics import fmean, pstdev
from typing import Literal

from pydantic import Field

from .models import (
    Contract,
    ProviderIssue,
    ProviderResult,
    ResearchRequest,
    canonical_symbol,
)

ScreenKind = Literal[
    "period_high",
    "period_low",
    "undervalued",
    "overvalued",
    "strong_buy",
    "strong_sell",
]
MAX_SYMBOLS = 100
NEAR_EXTREME_PCT = 5.0
STRETCH_Z = 1.0
LEAN_THRESHOLD = 20.0
DISCLAIMER = (
    "Research lean from price history only; not financial advice or an order. "
    "Window is the provider's recent period, not all-time."
)

# Bundled static subset of large S&P 500 members; not the full or current index.
SP500_TOP50 = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "BRK.B",
    "AVGO",
    "TSLA",
    "LLY",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "MA",
    "COST",
    "HD",
    "PG",
    "JNJ",
    "ORCL",
    "ABBV",
    "BAC",
    "CRM",
    "CVX",
    "KO",
    "WMT",
    "NFLX",
    "AMD",
    "MRK",
    "PEP",
    "TMO",
    "ADBE",
    "LIN",
    "CSCO",
    "ACN",
    "MCD",
    "ABT",
    "WFC",
    "GE",
    "IBM",
    "QCOM",
    "CAT",
    "DIS",
    "VZ",
    "INTU",
    "TXN",
    "AMGN",
    "PFE",
    "NOW",
    "GS",
)


class ScreenItem(Contract):
    symbol: str
    rank: int = Field(ge=1)
    score: float = Field(ge=0, le=100)
    last_close: float
    period_high: float
    period_high_date: date
    period_low: float
    period_low_date: date
    pct_below_high: float
    pct_above_low: float
    return_pct: float
    stretch_z: float
    reasoning: tuple[str, ...]


class ScreenResult(Contract):
    kind: ScreenKind
    window: str
    items: tuple[ScreenItem, ...]
    symbols_requested: int
    symbols_analyzed: int
    symbols_failed: tuple[str, ...] = ()
    symbols_rejected: tuple[str, ...] = ()
    note: str = DISCLAIMER


def metrics(symbol, data):
    bars = data.bars
    px = [b.adjusted_close if b.adjusted_close is not None else b.close for b in bars]
    hi, lo = (
        max(range(len(px)), key=px.__getitem__),
        min(range(len(px)), key=px.__getitem__),
    )
    last = px[-1]
    mean, sd = fmean(px), pstdev(px)
    n = min(21, len(px) - 1)
    ret = (last / px[-1 - n] - 1) * 100 if n else 0.0
    sma20, sma50 = fmean(px[-20:]), fmean(px[-50:])
    lean = max(
        -100.0,
        min(
            100.0,
            ret * 2 + (10 if last > sma20 else -10) + (10 if sma20 > sma50 else -10),
        ),
    )
    return {
        "symbol": symbol,
        "last": last,
        "hi": px[hi],
        "hi_date": bars[hi].session,
        "lo": px[lo],
        "lo_date": bars[lo].session,
        "below_high": (px[hi] - last) / px[hi] * 100,
        "above_low": (last - px[lo]) / px[lo] * 100,
        "ret": ret,
        "z": (last - mean) / sd if sd else 0.0,
        "lean": lean,
        "sessions": len(px),
        "start": bars[0].session,
        "end": bars[-1].session,
    }


def _select(kind, m):
    """Return (score, reasoning) when the symbol qualifies for the screen, else None."""
    if kind == "period_high" and m["below_high"] <= NEAR_EXTREME_PCT:
        d = m["below_high"]
        return (
            95 if d <= 1 else 85 if d <= 2 else 75,
            (f"{d:.1f}% below period high {m['hi']:.2f} ({m['hi_date']})",),
        )
    if kind == "period_low" and m["above_low"] <= NEAR_EXTREME_PCT:
        d = m["above_low"]
        return (
            95 if d <= 1 else 85 if d <= 2 else 75,
            (f"{d:.1f}% above period low {m['lo']:.2f} ({m['lo_date']})",),
        )
    if kind == "undervalued" and m["z"] <= -STRETCH_Z:
        return (
            min(100.0, 50 * -m["z"]),
            (
                f"Close is {-m['z']:.1f} std devs below the window mean (price stretch, not fundamentals)",
            ),
        )
    if kind == "overvalued" and m["z"] >= STRETCH_Z:
        return (
            min(100.0, 50 * m["z"]),
            (
                f"Close is {m['z']:.1f} std devs above the window mean (price stretch, not fundamentals)",
            ),
        )
    if kind == "strong_buy" and m["lean"] >= LEAN_THRESHOLD:
        return (
            m["lean"],
            (
                f"Momentum lean {m['lean']:.0f}: {m['ret']:+.1f}% over recent sessions, "
                "trend-average alignment",
            ),
        )
    if kind == "strong_sell" and m["lean"] <= -LEAN_THRESHOLD:
        return (
            -m["lean"],
            (
                f"Momentum lean {m['lean']:.0f}: {m['ret']:+.1f}% over recent sessions, "
                "trend-average alignment",
            ),
        )
    return None


# Closer-to-extreme sorts first for high/low; others rank by score.
_SORT = {
    "period_high": lambda m, s: m["below_high"],
    "period_low": lambda m, s: m["above_low"],
}


class ScreenService:
    def __init__(self, providers, concurrency=5, deadline_seconds=25.0):
        if not 1 <= concurrency <= 10:
            raise ValueError("concurrency must be 1..10")
        self.providers, self.concurrency, self.deadline = (
            providers,
            concurrency,
            deadline_seconds,
        )

    @staticmethod
    def universe():
        return SP500_TOP50

    async def sp500_list(self, limit=50):
        return SP500_TOP50[: min(max(limit, 1), len(SP500_TOP50))]

    async def screen(
        self, kind, symbols=None, limit=10
    ) -> ProviderResult[ScreenResult]:
        if kind not in ScreenKind.__args__:
            raise ValueError("unsupported screen")
        limit = min(max(int(limit), 1), 50)
        valid, rejected = [], []
        for raw in dict.fromkeys(symbols if symbols is not None else SP500_TOP50):
            try:
                valid.append(canonical_symbol(raw))
            except ValueError:
                rejected.append(str(raw)[:8])
        valid = valid[:MAX_SYMBOLS]
        sem = asyncio.Semaphore(self.concurrency)

        async def load(symbol):
            async with sem:
                return await self.providers.market(ResearchRequest(symbol=symbol))

        tasks = {s: asyncio.ensure_future(load(s)) for s in valid}
        if tasks:
            _, pending = await asyncio.wait(tasks.values(), timeout=self.deadline)
            for task in pending:
                task.cancel()
        analyzed, failed, issues = [], [], []
        for symbol, task in tasks.items():
            if not task.done() or task.cancelled() or task.exception() is not None:
                failed.append(symbol)
                continue
            result = task.result()
            if result.value is None:
                failed.append(symbol)
                issues.extend(result.issues)
            else:
                analyzed.append(metrics(symbol, result.value))
        issues = tuple(dict.fromkeys(issues))[:32]
        if not analyzed:
            return ProviderResult[ScreenResult](
                status="unavailable",
                issues=issues
                or (
                    ProviderIssue(
                        source_id="yahoo-chart",
                        code="unavailable",
                        message="No market data available for any screened symbol.",
                    ),
                ),
            )
        rows = []
        for m in analyzed:
            picked = _select(kind, m)
            if picked:
                rows.append((m, *picked))
        rows.sort(
            key=lambda r: (
                _SORT[kind](r[0], r[1]) if kind in _SORT else -r[1],
                r[0]["symbol"],
            )
        )
        items = tuple(
            ScreenItem(
                symbol=m["symbol"],
                rank=i,
                score=round(score, 2),
                last_close=m["last"],
                period_high=m["hi"],
                period_high_date=m["hi_date"],
                period_low=m["lo"],
                period_low_date=m["lo_date"],
                pct_below_high=round(m["below_high"], 2),
                pct_above_low=round(m["above_low"], 2),
                return_pct=round(m["ret"], 2),
                stretch_z=round(m["z"], 2),
                reasoning=reason,
            )
            for i, (m, score, reason) in enumerate(rows[:limit], 1)
        )
        sessions = max(m["sessions"] for m in analyzed)
        start, end = min(m["start"] for m in analyzed), max(m["end"] for m in analyzed)
        value = ScreenResult(
            kind=kind,
            window=f"period high/low over up to {sessions} daily sessions ({start} to {end})",
            items=items,
            symbols_requested=len(valid),
            symbols_analyzed=len(analyzed),
            symbols_failed=tuple(failed),
            symbols_rejected=tuple(rejected),
        )
        if failed or rejected:
            return ProviderResult[ScreenResult](
                status="partial",
                value=value,
                issues=issues
                or (
                    ProviderIssue(
                        source_id="yahoo-chart",
                        code="incomplete",
                        message="Some symbols were rejected, failed, or missed the deadline.",
                    ),
                ),
            )
        return ProviderResult[ScreenResult](status="completed", value=value)
