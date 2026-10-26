"""Vault-backed storage for the multi-agent layer.

Pure functions over a DataVault (no Streamlit imports), mirroring the
pattern in src/interface/chat_sessions.py so the storage logic is
unit-testable against a real temp vault. Every record type is an
encrypted vault key - see docs/multi_agent_design.md §6 for schemas and
docs/security_design.md for why (no new plaintext-at-rest locations).

Keys used:
  agents_index        {"agents": [Agent, ...]}
  mcp_servers         {"servers": [McpServer, ...]}
  agent_activity_log  {"entries": [ActivityEntry, ...]}  (capped)
  agent_run_<sid>     transient in-flight run state (see runtime.py)
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config import DEFAULT_CHAT_MODEL
from src.data_vault import DataVaultError

AGENTS_INDEX_KEY = "agents_index"
MCP_SERVERS_KEY = "mcp_servers"
ACTIVITY_LOG_KEY = "agent_activity_log"

# The built-in agent that preserves the pre-multi-agent behavior. It is a
# code constant, NOT a vault record: not editable, not deletable, and
# present even on a brand-new vault.
GENERAL_AGENT_ID = "general"

AGENT_ACTIVITY_MAX_ENTRIES = 500


def _now() -> str:
    return datetime.now().isoformat()


def _load(vault: Any, key: str, default: Dict[str, Any]) -> Dict[str, Any]:
    """Load a vault record, degrading to `default` on any failure (a
    decrypt error under a changed vault key must never crash a page)."""
    try:
        data = vault.retrieve_data(key)
    except DataVaultError:
        return default
    if not isinstance(data, dict):
        return default
    return data


def _save(vault: Any, key: str, data: Dict[str, Any]) -> None:
    vault.store_data(key, data)


def general_agent() -> Dict[str, Any]:
    """The built-in 'General' agent: exactly today's assistant behavior
    (RAG on, grounding system prompt, no tools)."""
    from src.ai_engine.chat_engine import ChatEngine

    return {
        "id": GENERAL_AGENT_ID,
        "name": "General",
        "description": "Grounded assistant over your uploaded documents",
        "icon": ":material/smart_toy:",
        "model": DEFAULT_CHAT_MODEL,
        "temperature": 0.3,
        "max_tokens": 2048,
        "context_window_tokens": 0,  # 0 = use OllamaClient default
        "instructions": ChatEngine.SYSTEM_PROMPT,
        "rag_enabled": True,
        "rag_scope_categories": [],
        "mcp_server_ids": [],
        "skills": [],
        "max_tool_rounds": 0,  # 0 = no tools (general never gets tools)
        "require_tool_confirmation": False,
        "built_in": True,
    }


def new_agent_defaults() -> Dict[str, Any]:
    """Blank fields for the 'New Agent' form (id/timestamps assigned on save)."""
    return {
        "id": "",
        "name": "",
        "description": "",
        "icon": ":material/agent_duo:",
        "model": DEFAULT_CHAT_MODEL,
        "temperature": 0.3,
        "max_tokens": 2048,
        "context_window_tokens": 0,
        "instructions": "",
        "rag_enabled": True,
        "rag_scope_categories": [],
        "mcp_server_ids": [],
        "skills": [],
        "max_tool_rounds": 6,
        "require_tool_confirmation": True,
        "built_in": False,
    }


def _index(vault: Any, key: str, item_key: str) -> Dict[str, Any]:
    index = _load(vault, key, {})
    index.setdefault(item_key, [])
    return index


def list_agents(vault: Any) -> List[Dict[str, Any]]:
    """All custom agents, creation order (UI adds General in front)."""
    return list(_load(vault, AGENTS_INDEX_KEY, {}).get("agents", []))


def get_agent(vault: Any, agent_id: str) -> Optional[Dict[str, Any]]:
    if agent_id == GENERAL_AGENT_ID:
        return general_agent()
    if not agent_id:
        return None
    if agent_id.startswith("builtin-"):
        import copy

        from src.agents.orchestration.templates import BUILTIN_AGENTS

        found = BUILTIN_AGENTS.get(agent_id)
        return copy.deepcopy(found) if found else None
    for agent in list_agents(vault):
        if agent.get("id") == agent_id:
            return agent
    return None


def create_agent(vault: Any, agent: Dict[str, Any]) -> str:
    """Persist a new agent; returns its new id."""
    now = _now()
    agent = dict(agent)
    agent["id"] = uuid.uuid4().hex
    agent["built_in"] = False
    agent["created_at"] = now
    agent["updated_at"] = now
    index = _index(vault, AGENTS_INDEX_KEY, "agents")
    index["agents"].append(agent)
    _save(vault, AGENTS_INDEX_KEY, index)
    return agent["id"]


def update_agent(vault: Any, agent_id: str, agent: Dict[str, Any]) -> bool:
    """Replace an agent's fields by id. Built-in and unknown ids: no-op."""
    if agent_id == GENERAL_AGENT_ID:
        return False
    index = _index(vault, AGENTS_INDEX_KEY, "agents")
    for stored in index["agents"]:
        if stored.get("id") == agent_id:
            agent = dict(agent)
            agent["id"] = agent_id
            agent["built_in"] = False
            agent["created_at"] = stored.get("created_at", _now())
            agent["updated_at"] = _now()
            index["agents"][index["agents"].index(stored)] = agent
            _save(vault, AGENTS_INDEX_KEY, index)
            return True
    return False


def delete_agent(vault: Any, agent_id: str) -> bool:
    if agent_id == GENERAL_AGENT_ID:
        return False
    index = _index(vault, AGENTS_INDEX_KEY, "agents")
    remaining = [a for a in index["agents"] if a.get("id") != agent_id]
    if len(remaining) == len(index["agents"]):
        return False
    index["agents"] = remaining
    _save(vault, AGENTS_INDEX_KEY, index)
    return True


def list_mcp_servers(vault: Any) -> List[Dict[str, Any]]:
    return list(_load(vault, MCP_SERVERS_KEY, {}).get("servers", []))


def get_mcp_server(vault: Any, server_id: str) -> Optional[Dict[str, Any]]:
    for server in list_mcp_servers(vault):
        if server.get("id") == server_id:
            return server
    return None


def save_mcp_server(vault: Any, server: Dict[str, Any]) -> str:
    """Upsert an MCP server definition; returns its id (new or existing)."""
    server = dict(server)
    index = _index(vault, MCP_SERVERS_KEY, "servers")
    existing_id = server.get("id") or ""
    for stored in index["servers"]:
        if stored.get("id") == existing_id:
            server["id"] = existing_id
            server["created_at"] = stored.get("created_at", _now())
            server["updated_at"] = _now()
            index["servers"][index["servers"].index(stored)] = server
            _save(vault, MCP_SERVERS_KEY, index)
            return existing_id
    server["id"] = uuid.uuid4().hex
    now = _now()
    server["created_at"] = now
    server["updated_at"] = now
    index["servers"].append(server)
    _save(vault, MCP_SERVERS_KEY, index)
    return server["id"]


def update_mcp_server_status(
    vault: Any, server_id: str, **fields: Any
) -> bool:
    """Patch volatile connection fields (last_status, last_error, ...)."""
    index = _index(vault, MCP_SERVERS_KEY, "servers")
    for stored in index["servers"]:
        if stored.get("id") == server_id:
            stored.update(fields)
            stored["updated_at"] = _now()
            _save(vault, MCP_SERVERS_KEY, index)
            return True
    return False


def delete_mcp_server(vault: Any, server_id: str) -> bool:
    index = _index(vault, MCP_SERVERS_KEY, "servers")
    remaining = [s for s in index["servers"] if s.get("id") != server_id]
    if len(remaining) == len(index["servers"]):
        return False
    index["servers"] = remaining
    _save(vault, MCP_SERVERS_KEY, index)
    return True


def log_activity(vault: Any, entry: Dict[str, Any], max_entries: int = AGENT_ACTIVITY_MAX_ENTRIES) -> None:
    """Append one activity entry, dropping the oldest beyond the cap.
    Failures are swallowed on purpose: an audit log must never break the
    chat turn that produced it."""
    try:
        index = _load(vault, ACTIVITY_LOG_KEY, {})
        entries = list(index.get("entries", []))
        entries.append({"ts": _now(), **entry})
        index["entries"] = entries[-max_entries:]
        _save(vault, ACTIVITY_LOG_KEY, index)
    except Exception:
        pass


def list_activity(vault: Any, limit: int = 100) -> List[Dict[str, Any]]:
    """Newest-first activity entries."""
    entries = list(_load(vault, ACTIVITY_LOG_KEY, {}).get("entries", []))
    return list(reversed(entries))[:limit]


def run_key_for(session_id: str) -> str:
    return f"agent_run_{session_id}"


def save_run_state(vault: Any, session_id: str, state: Dict[str, Any]) -> None:
    _save(vault, run_key_for(session_id), {"created_at": _now(), **state})


def load_run_state(vault: Any, session_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = vault.retrieve_data(run_key_for(session_id))
    except DataVaultError:
        return None
    return data if isinstance(data, dict) else None


def delete_run_state(vault: Any, session_id: str) -> None:
    try:
        vault.delete_data(run_key_for(session_id))
    except Exception:
        pass
