"""LLM strategy agents (one tool-less slot per day-trader skill) and the reviewer. Agents only choose among closed
options: take/skip, a stop rule from the strategy's list, a target R, a predicted outcome, a confidence and a closed
reason. Code builds the prompt from numbers, validates the reply, and alone sets entry, size and risk (S24)."""
import inspect
import json
import time
from typing import Literal, Optional

from pydantic import Field, ValidationError, field_validator

from src import config
from src.forecaster.models import Contract
from src.forecaster.scenarios import _Invalid, parse_model_json
from src.trader.strategies import STOP_RULES, TARGET_RS
from src.observability import otel

SLOT_IDS = {"orb": "forecast.trader.orb", "breakout20": "forecast.trader.breakout", "bounce": "forecast.trader.bounce"}
REVIEW_SLOT = "forecast.trader.reviewer"
REASONS = (
    "clean_setup", "strong_odds", "weak_odds", "low_volume", "extended", "choppy_range", "trend_aligned",
    "against_trend", "good_history", "poor_history", "high_volatility",
)
SLOT_RULES = (
    "You decide on ONE paper-trading signal for a research simulation; no real money and no order exists. "
    "SIGNAL holds numbers computed by code from past bars only; the ticker and date are hidden on purpose. No tools. "
    "Reply with ONE JSON object matching the schema and nothing else: action take|skip, stop_rule from ALLOWED_STOP_RULES, "
    "target_r from ALLOWED_TARGET_R, predicted target|stop|neither (what you expect happens first if taken), confidence 0-1 "
    "that your prediction is right, reason from the schema's list. You never choose prices or share counts."
)


class TraderDecision(Contract):
    action: Literal["take", "skip"]
    stop_rule: str = Field(max_length=8)
    target_r: float
    predicted: Literal["target", "stop", "neither"]
    confidence: float = Field(ge=0, le=1)
    reason: Literal[REASONS]

    @field_validator("target_r")
    @classmethod
    def _r(cls, v):
        if v not in TARGET_RS:
            raise ValueError("target_r not allowed")
        return float(v)


class TraderReview(Contract):
    verdict: Literal["beats_baseline", "matches_baseline", "trails_baseline", "too_few_trades"]
    suggestion: Literal["no_change", "skip_more", "skip_less", "wider_stop", "tighter_stop", "smaller_target", "larger_target"]
    confidence: float = Field(ge=0, le=1)


def _client(factory, agent):
    params = inspect.signature(factory).parameters
    kw = next((n for n in ("remaining_seconds", "timeout_seconds") if n in params), None)
    client = factory(**({kw: config.TRADER_AGENT_CALL_SECONDS} if kw else {}))
    client.max_tokens = min(int(agent.get("max_tokens") or config.TRADER_AGENT_MAX_TOKENS), config.TRADER_AGENT_MAX_TOKENS)
    client.temperature = min(max(float(agent.get("temperature", 0.0)), 0.0), 1.0)
    if agent.get("model") and hasattr(client, "model"):
        client.model = str(agent["model"])
    return client


def _ask(factory, agent, user: str, schema) -> tuple[Optional[dict], dict]:
    meta = {"agent_id": agent.get("id"), "status": "completed", "invalid_reason": None, "model_id": None}
    start = time.monotonic()
    try:
        client = _client(factory, agent)
        meta["model_id"] = getattr(client, "model", None)
        messages = [
            {"role": "system", "content": SLOT_RULES + "\n\n" + str(agent.get("instructions") or "")},
            {"role": "user", "content": user},
        ]
        resp = client.chat(messages=messages, stream=False, format=schema.model_json_schema())
        content = resp["message"]["content"]
        if isinstance(content, str) and len(content) > 4000:
            raise _Invalid("truncated")
        raw = parse_model_json(content)
        if type(raw.get("confidence")) not in (int, float):
            raise _Invalid("schema")
        try:
            return schema.model_validate(raw).model_dump(), meta | {"latency_ms": int((time.monotonic() - start) * 1000)}
        except ValidationError:
            raise _Invalid("schema")
    except _Invalid as e:
        meta.update(status="invalid", invalid_reason=e.reason)
    except TimeoutError:
        meta.update(status="timeout")
    except Exception:
        meta.update(status="error")
    meta["latency_ms"] = int((time.monotonic() - start) * 1000)
    return None, meta


def prompt(signal: dict) -> str:
    """Numbers only: no symbol, date, price level or path (tested)."""
    s = signal["strategy"]
    return (
        f"STRATEGY:{s}\nALLOWED_STOP_RULES:{json.dumps(STOP_RULES[s])}\nALLOWED_TARGET_R:{json.dumps(TARGET_RS)}\n"
        f"SIGNAL:{json.dumps(signal['features'], sort_keys=True)}"
    )


@otel.traced("trader.paper_agent")
def decide(factory, agent, signal) -> tuple[Optional[dict], dict]:
    out, meta = _ask(factory, agent, prompt(signal), TraderDecision)
    if out and out["stop_rule"] not in STOP_RULES[signal["strategy"]]:
        out, meta = None, meta | {"status": "invalid", "invalid_reason": "schema"}
    return out, meta


def review(factory, agent, strategy: str, agent_stats: dict, baseline_stats: dict) -> tuple[Optional[dict], dict]:
    keep = ("taken", "win_rate", "pnl", "avg_r", "max_drawdown", "prediction_accuracy", "skip_saved")
    user = (
        f"STRATEGY:{strategy}\nAGENT:{json.dumps({k: agent_stats.get(k) for k in keep})}\n"
        f"BASELINE_TAKES_EVERY_SIGNAL:{json.dumps({k: baseline_stats.get(k) for k in keep})}"
    )
    return _ask(factory, agent, user, TraderReview)
