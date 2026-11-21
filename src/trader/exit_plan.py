import math


def plan(entry: float, or_low: float, or_high: float, stop: str = "or-mid", target_r: float = 2.0) -> dict:
    """Stop rounded down, target = entry + target_r x risk. Refuses a stop at/above entry or a stop under 0.05% risk."""
    stops = {"or-mid": (or_low + or_high) / 2, "or-low": or_low}
    if stop.startswith("pct:"):
        level = entry * (1 - float(stop[4:]) / 100)
    elif stop in stops:
        level = stops[stop]
    else:
        raise ValueError("unknown stop rule")
    level = math.floor(level * 100) / 100
    risk = round(entry - level, 4)
    if risk <= 0:
        raise ValueError("stop must be below the entry")
    if risk < entry * 0.0005:
        raise ValueError("stop too tight to survive the spread")
    if target_r <= 0:
        raise ValueError("target must be above the entry")
    return {"entry": entry, "stop": level, "target": round(entry + target_r * risk, 2), "risk": risk, "target_r": target_r}
