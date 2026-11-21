"""Draft US exchange session list for outcome tracking (offline, rule-based).

A DRAFT the user must still verify against the official NYSE/Nasdaq calendar and attest: rules cover the regular
holidays but not one-off closures (e.g. national days of mourning) or early 13:00 closes (16:00 is used, which only
delays when a session counts as completed - the conservative direction)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import TradingSession

NY = ZoneInfo("America/New_York")


def _easter(year: int) -> date:  # anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


def _nth(year, month, weekday, n):  # n-th weekday (0=Mon) of a month; n=-1 = last
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date, *, saturday_to_friday=True):
    if day.weekday() == 5:
        return day - timedelta(days=1) if saturday_to_friday else None
    return day + timedelta(days=1) if day.weekday() == 6 else day


def nyse_holidays(year: int) -> set:
    days = {
        _observed(
            date(year, 1, 1), saturday_to_friday=False
        ),  # NYSE does not close Fri Dec 31
        _nth(year, 1, 0, 3),
        _nth(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _nth(year, 5, 0, -1),
        _observed(date(year, 7, 4)),
        _nth(year, 9, 0, 1),
        _nth(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    return {d for d in days if d}


def draft_sessions(
    exchange: str, around: date, before: int = 5, after: int = 45
) -> tuple[TradingSession, ...]:
    """`before` sessions up to and including `around` (if it is a session), then `after` sessions."""

    def is_session(d):
        return d.weekday() < 5 and d not in nyse_holidays(d.year)

    past, d = [], around
    while len(past) < before:
        if is_session(d):
            past.append(d)
        d -= timedelta(days=1)
    future, d = [], around + timedelta(days=1)
    while len(future) < after:
        if is_session(d):
            future.append(d)
        d += timedelta(days=1)
    return tuple(
        TradingSession(
            exchange=exchange, session=s, closes_at=datetime.combine(s, time(16), NY)
        )
        for s in sorted(past) + future
    )
