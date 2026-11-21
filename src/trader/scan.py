"""Setup scanner over bars already fetched. Pure functions; universe comes from the caller (typed tickers / Robinhood import)."""
from src.trader.bars import Bar, by_session
from src.trader.entry import check


def daily_closes(bars: list[Bar]) -> list[float]:
    return [day[-1].c for _, day in sorted(by_session(bars).items())]


def _rsi(closes, n=14):
    gains = [max(b - a, 0) for a, b in zip(closes, closes[1:])][-n:]
    losses = [max(a - b, 0) for a, b in zip(closes, closes[1:])][-n:]
    if len(gains) < n:
        return None
    ag, al = sum(gains) / n, sum(losses) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def daily_setups(closes: list[float]) -> list[str]:
    """breakout20 (close above the prior 20 closes), reclaim20 (back above the 20-day average), oversold (RSI14 < 30)."""
    if len(closes) < 22:
        return []
    out, last, prev = [], closes[-1], closes[-2]
    if last > max(closes[-21:-1]):
        out.append("breakout20")
    sma, sma_prev = sum(closes[-20:]) / 20, sum(closes[-21:-1]) / 20
    if last > sma and prev <= sma_prev:
        out.append("reclaim20")
    r = _rsi(closes)
    if r is not None and r < 30:
        out.append("oversold")
    return out


def orb_scan(bars_by_symbol: dict[str, list[Bar]]) -> dict[str, dict]:
    """Symbols whose latest session has triggered an opening-range breakout."""
    hits = {}
    for sym, bars in bars_by_symbol.items():
        days = by_session(bars)
        if not days:
            continue
        res = check(days[max(days)])
        if res["state"] == "triggered":
            hits[sym] = res
    return hits
