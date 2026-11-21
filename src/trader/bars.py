from datetime import date, datetime, time
from typing import NamedTuple
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)


class Bar(NamedTuple):
    t: datetime  # New York, start of the 5-minute bar
    o: float
    h: float
    l: float
    c: float
    v: float


def hhmm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def parse_yahoo_5m(payload: dict, symbol: str, intraday: bool = True) -> list[Bar]:
    """Fail closed: wrong symbol or misaligned arrays raise; incomplete bars are skipped."""
    (res,) = payload["chart"]["result"]
    if res["meta"]["symbol"].replace("-", ".").upper() != symbol.upper():
        raise ValueError("intraday identity mismatch")
    q = res["indicators"]["quote"][0]
    ts = res["timestamp"]
    cols = [q[k] for k in ("open", "high", "low", "close", "volume")]
    if any(len(c) != len(ts) for c in cols):
        raise ValueError("misaligned intraday arrays")
    out = []
    for t, *row in zip(ts, *cols):
        if any(x is None for x in row):
            continue
        dt = datetime.fromtimestamp(t, NY)
        if dt.weekday() < 5 and (not intraday or OPEN <= dt.time() < CLOSE):
            out.append(Bar(dt, *map(float, row)))
    return sorted(out)


def by_session(bars: list[Bar]) -> dict[date, list[Bar]]:
    days: dict[date, list[Bar]] = {}
    for b in bars:
        days.setdefault(b.t.date(), []).append(b)
    return days
