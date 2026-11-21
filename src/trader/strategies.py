"""Signal detection for the paper agents. Each signal's `features` are built ONLY from bars up to the signal (no lookahead)
and are relative numbers (percent, ratios, odds): no symbol, date or price level reaches an agent, so a model cannot lean on
what it remembers about a ticker's history. `path` (what happened next) is kept apart for resolution only."""
import hashlib
import math

from src import config
from src.trader import entry as orb_entry
from src.trader import exit_plan, timemachine
from src.trader.bars import Bar, by_session, hhmm
from src.trader.scan import _rsi

STRATEGIES = ("orb", "breakout20", "bounce")
STOP_RULES = {"orb": ("or-mid", "or-low"), "breakout20": ("atr1", "atr2", "pct3"), "bounce": ("atr1", "atr2", "pct3")}
DEFAULT_PLAN = {"orb": ("or-mid", 2.0), "breakout20": ("atr2", 2.0), "bounce": ("atr2", 2.0)}
TARGET_RS = (1.0, 1.5, 2.0, 3.0)


def _id(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if b else 0.0


def _atr(bars: list[Bar], n=14):
    trs = [max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)) for p, b in zip(bars, bars[1:])][-n:]
    return sum(trs) / len(trs) if trs else None


def stop_level(strategy, rule, entry, levels) -> float:
    if strategy == "orb":
        return exit_plan.plan(entry, levels["or_low"], levels["or_high"], rule, 1.0)["stop"]
    raw = {"atr1": entry - levels["atr"], "atr2": entry - 2 * levels["atr"], "pct3": entry * 0.97}[rule]
    return math.floor(raw * 100) / 100


def resolve(path, entry, stop, target, complete) -> tuple:
    """(outcome, exit price, exit time). A bar touching both counts as the stop (conservative, as upstream)."""
    for h, l, c, t in path:
        if l <= stop:
            return "stop", stop, t
        if h >= target:
            return "target", target, t
    if not complete or not path:
        return "open", None, None
    return "neither", path[-1][2], path[-1][3]


# --- opening-range breakout on 5-minute bars ------------------------------------------------------------------------


def orb_signals(symbol: str, bars: list[Bar]) -> list[dict]:
    days = by_session(bars)
    dates = sorted(days)
    flat = hhmm(config.TRADER_FLAT_BY)
    out = []
    for k, d in enumerate(dates):
        day = days[d]
        res = orb_entry.check(day)
        if res["state"] != "triggered":
            continue
        i, e = res["index"], res["entry"]
        past = {x: days[x] for x in dates[:k]}  # sessions strictly before this one
        rng = day[: orb_entry.opening_range(day)[2]]
        feats = {
            "or_range_pct": round((res["or_high"] - res["or_low"]) / e * 100, 3),
            "breakout_pct": _pct(e, res["or_high"]),
            "trigger_volume_x": round(day[i].v / (sum(b.v for b in rng) / len(rng) or 1), 2),
            "minutes_after_open": (day[i].t.hour - 9) * 60 + day[i].t.minute - 30 + 5,
            "gap_pct": _pct(day[0].o, days[dates[k - 1]][-1].c) if k else 0.0,
            "prior_session_return_pct": _pct(days[dates[k - 1]][-1].c, days[dates[k - 1]][0].o) if k else 0.0,
        }
        plan = exit_plan.plan(e, res["or_low"], res["or_high"], "or-mid", 2.0)
        if past:
            try:
                mc = timemachine.simulate(past, plan, day[i].t, draws=config.TRADER_SIGNAL_MC_DRAWS)
                feats.update(sim_target_first=mc["target_first"], sim_stop_first=mc["stop_first"],
                             sim_edge_2r=round(timemachine.edge(mc, 2.0) or 0.0, 3))
            except ValueError:
                pass
            hist = timemachine.replay(
                past,
                lambda x: (lambda r: r if r["state"] == "triggered" else None)(orb_entry.check(x)),
                lambda r: exit_plan.plan(r["entry"], r["or_low"], r["or_high"], "or-mid", 2.0),
            )
            feats.update(history_fired=hist["fired"], history_target=hist["target"], history_stop=hist["stop"])
        after = [b for b in day[i + 1 :] if b.t.time() <= flat]
        out.append({
            "id": _id("orb", symbol, day[i].t.isoformat()),
            "strategy": "orb", "symbol": symbol, "at": day[i].t.isoformat(), "date": d.isoformat(), "entry": e,
            "levels": {"or_low": res["or_low"], "or_high": res["or_high"]}, "features": feats, "window": True,
            "path": [(b.h, b.l, b.c, b.t.isoformat()) for b in after],
            "complete": bool(after) and after[-1].t.time() >= flat,
        })
    return out


# --- daily setups (upstream trader-scanner rules on closes), held up to TRADER_SWING_MAX_HOLD sessions -------------


def _fires(strategy, closes) -> bool:
    if len(closes) < 22:
        return False
    if strategy == "breakout20":
        return closes[-1] > max(closes[-21:-1])
    r_prev, r_now = _rsi(closes[:-1]), _rsi(closes)
    return strategy == "bounce" and r_prev is not None and r_prev < 30 and closes[-1] > closes[-2] and r_now is not None


def _daily_outcome(bars, i, rule, target_r, strategy):
    """Replay helper for the history feature (bars strictly up to the caller's signal)."""
    hold = config.TRADER_SWING_MAX_HOLD
    if i + 1 >= len(bars):
        return None
    e, atr = bars[i + 1].o, _atr(bars[: i + 1])
    if not atr:
        return None
    stop = stop_level(strategy, rule, e, {"atr": atr})
    path = [(b.h, b.l, b.c, b.t.isoformat()) for b in bars[i + 1 : i + 1 + hold]]
    return resolve(path, e, stop, e + target_r * (e - stop), len(path) == hold)[0]


def daily_signals(symbol: str, bars: list[Bar], strategy: str) -> list[dict]:
    hold = config.TRADER_SWING_MAX_HOLD
    closes = [b.c for b in bars]
    out = []
    for i in range(30, len(bars)):
        if not _fires(strategy, closes[: i + 1]):
            continue
        seen = bars[: i + 1]
        c = closes[: i + 1]
        atr = _atr(seen)
        if not atr:
            continue
        sma20 = sum(c[-20:]) / 20
        sma50 = sum(c[-50:]) / len(c[-50:])
        vol20 = sum(b.v for b in seen[-21:-1]) / 20 or 1
        rule, r = DEFAULT_PLAN[strategy]
        prior = [o for j in range(30, i - hold) if _fires(strategy, c[: j + 1]) for o in [_daily_outcome(seen, j, rule, r, strategy)] if o]
        feats = {
            "return_5d_pct": _pct(c[-1], c[-6]), "return_20d_pct": _pct(c[-1], c[-21]),
            "rsi14": round(_rsi(c) or 0.0, 1), "dist_sma20_pct": _pct(c[-1], sma20), "dist_sma50_pct": _pct(c[-1], sma50),
            "atr_pct": round(atr / c[-1] * 100, 2), "volume_x": round(seen[-1].v / vol20, 2),
            "off_high_pct": _pct(c[-1], max(c)), "history_fired": len(prior),
            "history_target": prior.count("target"), "history_stop": prior.count("stop"),
        }
        nxt = bars[i + 1] if i + 1 < len(bars) else None
        e = nxt.o if nxt else c[-1]  # forward: next open unknown yet -> last close until the next run
        path = [(b.h, b.l, b.c, b.t.isoformat()) for b in bars[i + 1 : i + 1 + hold]]
        out.append({
            "id": _id(strategy, symbol, seen[-1].t.date().isoformat()),
            "strategy": strategy, "symbol": symbol, "at": seen[-1].t.isoformat(), "date": seen[-1].t.date().isoformat(),
            "entry": e, "levels": {"atr": atr}, "features": feats, "window": False,
            "path": path, "complete": len(path) == hold,
        })
    return out
