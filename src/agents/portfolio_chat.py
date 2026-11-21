"""Chat agent "Robinhood Portfolio" (role `portfolio`, S25). Code reads holdings through `RobinhoodClient` (exact-name
account tools only, R-S1/S20) on the calling thread and computes the figures; a tool-less LOCAL model answers from
them. Account numbers never enter the prompt; the activity log gets status and timing only."""
import json
import re
import time

from src import config
from src.forecaster import robinhood as rh

RULES = (
    "Answer the user's question about their own portfolio using only PORTFOLIO, which code computed from their "
    "Robinhood account (read-only). No tools. If the answer is not in the figures, say so. Context, not advice: never "
    "say buy, sell, add, trim or hold and never give order instructions."
)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def snapshot(client) -> dict:
    accounts, held = rh.holdings(client)
    total = sum(h.market_value or 0 for h in held)
    rows = []
    for h in sorted(held, key=lambda h: -(h.market_value or 0))[: config.PORTFOLIO_CHAT_MAX_POSITIONS]:
        rows.append({
            "symbol": h.symbol,
            "quantity": round(h.quantity, 4),
            "average_cost": None if h.average_cost is None else round(h.average_cost, 2),
            "market_value": None if h.market_value is None else round(h.market_value, 2),
            "weight_pct": round(h.market_value / total * 100, 2) if total and h.market_value else None,
            "unrealized_pct": round((h.market_value / (h.average_cost * h.quantity) - 1) * 100, 2)
            if h.market_value and h.average_cost else None,
        })
    return {"accounts": len(accounts), "positions": len(held), "total_market_value": round(total, 2), "holdings": rows}


def answer(factory, agent: dict, question: str, snap: dict, history=()) -> str:
    client = factory(remaining_seconds=config.PORTFOLIO_CHAT_SECONDS)
    client.max_tokens = min(int(agent.get("max_tokens") or config.PORTFOLIO_CHAT_MAX_TOKENS), config.PORTFOLIO_CHAT_MAX_TOKENS)
    client.temperature = min(max(float(agent.get("temperature", 0.2)), 0.0), 1.0)
    if agent.get("model") and hasattr(client, "model"):
        client.model = str(agent["model"])
    turns = [{"role": m["role"], "content": str(m["content"])[:2000]} for m in list(history)[-config.PORTFOLIO_CHAT_HISTORY_TURNS:]
             if m.get("role") in ("user", "assistant")]
    messages = [
        {"role": "system", "content": RULES + "\n\n" + str(agent.get("instructions") or "")},
        *turns,
        {"role": "user", "content": f"PORTFOLIO:{json.dumps(snap, sort_keys=True)}\n\nQUESTION:{question}"},
    ]
    resp = client.chat(messages=messages, stream=False)
    text = _THINK.sub("", str(resp["message"]["content"] or "")).strip()
    return text[:6000] or "The model returned an empty answer."


def respond(vault, mcp, factory, agent: dict, question: str, history=()) -> str:
    """One chat turn. Returns user-facing text; never raises with account detail."""
    from src.agents import store
    from src.agents.orchestration.policy import screen

    refused = screen(question)
    if refused:
        return refused.message
    client = rh.connect(vault, mcp)
    if client is None:
        return 'Robinhood is not connected: add an MCP server named "Robinhood" in Agents → MCP Servers and authorize it.'
    start, status = time.monotonic(), "completed"
    try:
        snap = snapshot(client)
        if not snap["positions"]:
            status = "empty"
            return "Robinhood returned no open stock positions."
        if factory is None:
            status = "no_model"
            return "No local model is configured, so the Portfolio agent cannot answer (figures are never sent off this machine)."
        return answer(factory, agent, question, snap, history)
    except Exception as exc:
        status = "error"
        return f"The Portfolio agent could not complete this ({type(exc).__name__}). Nothing was changed."
    finally:
        store.log_activity(vault, {"kind": "portfolio_chat", "status": status,
                                   "latency_ms": int((time.monotonic() - start) * 1000)})
