from src import config
from src.trader.bars import hhmm


def check(entry, stop, target, qty, at, trades_today=0, loss_today=0.0, window=True) -> dict:
    """PASS/BLOCK label for the plan. Rules come from config only; nothing here is loosened to pass (S22)."""
    why = []
    risk = entry - stop
    loss = qty * risk
    if stop >= entry:
        why.append("stop: not below the entry")
        return {"verdict": "BLOCK", "reasons": why, "max_loss": None, "reward_risk": None}
    rr = (target - entry) / risk
    if loss > config.TRADER_MAX_LOSS_USD:
        why.append("loss: $%.2f at the stop is over the $%.2f limit" % (loss, config.TRADER_MAX_LOSS_USD))
    if rr < config.TRADER_MIN_REWARD_RISK:
        why.append("reward:risk %.2f is under %.2f" % (rr, config.TRADER_MIN_REWARD_RISK))
    if loss_today + loss > config.TRADER_DAILY_MAX_LOSS_USD:
        why.append("daily loss: would pass the $%.2f daily limit" % config.TRADER_DAILY_MAX_LOSS_USD)
    if window and not hhmm(config.TRADER_NO_ENTRY_BEFORE) <= at.time() <= hhmm(config.TRADER_NO_ENTRY_AFTER) or at.weekday() > 4:
        why.append("window: outside %s-%s New York" % (config.TRADER_NO_ENTRY_BEFORE, config.TRADER_NO_ENTRY_AFTER))
    return {"verdict": "BLOCK" if why else "PASS", "reasons": why, "max_loss": round(loss, 2), "reward_risk": round(rr, 2)}
