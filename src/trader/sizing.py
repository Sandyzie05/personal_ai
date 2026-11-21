from src import config


def shares(entry, stop, max_loss=None, budget=None, max_pos_pct=None) -> dict:
    """Whole shares under max loss at the stop, the position cap and the budget. A stop too far away is a refusal."""
    max_loss = config.TRADER_MAX_LOSS_USD if max_loss is None else max_loss
    budget = config.TRADER_BUDGET_USD if budget is None else budget
    cap = budget * (config.TRADER_MAX_POSITION_PCT if max_pos_pct is None else max_pos_pct) / 100
    risk = entry - stop
    if risk <= 0:
        raise ValueError("stop must be below the entry")
    limits = {"max loss": int(max_loss // risk), "position cap": int(cap // entry), "budget": int(budget // entry)}
    limiter = min(limits, key=limits.get)
    qty = limits[limiter]
    if qty < 1:
        raise ValueError("stop too far for the %s: not even one share fits (%s)" % (limiter, limits))
    return {"qty": qty, "limiter": limiter, "max_loss": round(qty * risk, 2), "cost": round(qty * entry, 2)}
