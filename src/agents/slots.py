"""Workflow slots: every AI step in a workflow names a slot with a fixed default agent.

The user tweaks the slot's agent in the Agents tab (store overrides). The slot itself fixes what is NOT tweakable:
the tools it may use (none for forecast slots), the output schema (enforced by the calling code) and the budget.
Tools outside `allowed_tools` are dropped here, at call time, whatever the agent config lists.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.agents import store, skills


@dataclass(frozen=True)
class Slot:
    slot_id: str
    label: str
    default_agent: str
    output: str  # name of the schema the caller validates against
    allowed_tools: Tuple[str, ...] = ()


SLOTS: Dict[str, Slot] = {
    s.slot_id: s
    for s in (
        Slot(
            "forecast.critic.macro",
            "Macro critic",
            "builtin-critic-macro",
            "SpecialistOpinion",
        ),
        Slot(
            "forecast.critic.valuation",
            "Valuation critic",
            "builtin-critic-valuation",
            "SpecialistOpinion",
        ),
        Slot(
            "forecast.critic.event",
            "Event critic",
            "builtin-critic-event",
            "SpecialistOpinion",
        ),
        Slot(
            "forecast.critic.downside",
            "Downside critic",
            "builtin-critic-downside",
            "SpecialistOpinion",
        ),
        Slot(
            "forecast.thesis_explainer",
            "Thesis explainer",
            "builtin-thesis-explainer",
            "ThesisExplanation",
        ),
        Slot(
            "forecast.watchlist_explainer",
            "Watchlist explainer",
            "builtin-watchlist-explainer",
            "WatchlistExplanation",
        ),
        Slot(
            "forecast.news_analyst",
            "News analyst",
            "builtin-news-analyst",
            "NewsFacts",
        ),
    )
}
CRITIC_SLOTS = {
    role: f"forecast.critic.{role}"
    for role in ("macro", "valuation", "event", "downside")
}


def _skills_text(slugs) -> str:
    try:
        from src.agents.orchestration.skill_pack import install_builtin_skills

        install_builtin_skills()
    except OSError:
        pass
    bodies = [s["body"] for s in (skills.read_skill(slug) for slug in slugs or ()) if s]
    return "\n\n".join(bodies)


def agent_for(vault: Any, slot_id: str) -> Dict[str, Any]:
    """The slot's (tweaked) agent, with tools clamped to the slot and skills folded in before the safety rules."""
    slot = SLOTS[slot_id]
    agent = store.get_agent(vault, slot.default_agent) if vault is not None else None
    if agent is None:
        from src.agents.orchestration.templates import BUILTIN_AGENTS

        agent = dict(BUILTIN_AGENTS[slot.default_agent])
    if not slot.allowed_tools:
        agent["mcp_server_ids"], agent["max_tool_rounds"] = [], 0
    text = _skills_text(agent.get("skills"))
    agent["instructions"] = (text + "\n\n" if text else "") + agent["instructions"]
    agent["slot_id"] = slot_id
    return agent


def critic_agents(vault: Any) -> Dict[str, Dict[str, Any]]:
    return {role: agent_for(vault, slot_id) for role, slot_id in CRITIC_SLOTS.items()}


def log_run(
    vault: Any,
    slot_id: str,
    agent_id: Optional[str],
    status: str,
    latency_ms: Optional[int] = None,
    invalid_reason: Optional[str] = None,
) -> None:
    """Audit entry: ids, status and timing only - never prompt or model text."""
    if vault is not None:
        store.log_activity(
            vault,
            {
                "kind": "slot_run",
                "slot_id": slot_id,
                "agent_id": agent_id,
                "status": status,
                "latency_ms": latency_ms,
                "invalid_reason": invalid_reason,
            },
        )
