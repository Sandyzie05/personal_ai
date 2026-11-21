"""Opening-range breakout (ORB) entry timer. Long only. Never places anything."""
from datetime import datetime, timedelta

from src import config
from src.trader.bars import Bar, hhmm


def opening_range(day: list[Bar], minutes: int = 15):
    """(low, high, first index after the range) or None while the range is still forming."""
    if not day:
        return None
    end = datetime.combine(day[0].t.date(), hhmm("09:30"), tzinfo=day[0].t.tzinfo) + timedelta(minutes=minutes)
    rng = [b for b in day if b.t < end]
    n = len(rng)
    if n == len(day) or n < minutes // 5:  # no bar at/after the range end yet
        return None
    return min(b.l for b in rng), max(b.h for b in rng), n


def check(day: list[Bar], minutes: int = 15) -> dict:
    """waiting | triggered | expired. Triggered = first bar after the range that CLOSES above the range high."""
    r = opening_range(day, minutes)
    if r is None:
        return {"state": "waiting", "reason": "opening range still forming"}
    low, high, n = r
    expires = hhmm(config.TRADER_ENTRY_EXPIRES)
    for b in day[n:]:
        if b.t.time() > expires:
            break
        if b.c > high:
            return {"state": "triggered", "entry": b.c, "at": b.t, "or_low": low, "or_high": high, "index": day.index(b)}
    last = day[-1].t.time()
    state = "expired" if last >= expires else "waiting"
    return {"state": state, "or_low": low, "or_high": high, "reason": "no close above the range high yet"}
