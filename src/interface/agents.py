"""Agents page: manage agents, MCP servers, and skills; view tool activity.

Thin Streamlit projection over the pure logic in `src/agents/*` (store,
skills, mcp) - all vault access goes through `store`, all MCP access through
one session-cached `McpManager`. Conventions per AGENTS.md: one Save per
form, no deep expander nesting, Material Symbols icons, no new theme colors.
"""

from typing import Any, Dict, List, Optional

import streamlit as st

from src.agents import skills
from src.agents import store
from src.agents.mcp import McpError, McpManager
from src.config import (
    DEFAULT_AGENT_MAX_TOOL_ROUNDS,
    DEFAULT_CHAT_MODEL,
    MCP_OAUTH_TIMEOUT_SECONDS,
)
from src.data_extraction import category_keys, get_category

_SERVER_STATUS = {
    "connected": (":material/check_circle:", "Connected"),
    "needs_auth": (":material/key:", "Needs authorization"),
    "error": (":material/warning:", "Error"),
    "disabled": (":material/lock:", "Disabled"),
}


def _mcp_manager(vault: Any) -> McpManager:
    """Session-cached McpManager (Streamlit reruns this whole module on
    every interaction). Reused across reruns as long as it belongs to the
    current vault instance - a lock/unlock cycle replaces the vault."""
    cached = st.session_state.get("mcp_manager")
    if cached is None or getattr(cached, "vault", None) is not vault:
        cached = McpManager(vault)
        st.session_state["mcp_manager"] = cached
    return cached


def _ollama_client(ollama_client: Optional[Any], vault: Any) -> Any:
    """A client capable of listing models: the chat engine's when
    available, otherwise a throwaway one (construction makes no network
    calls - only list_models/model_details do)."""
    if ollama_client is not None:
        return ollama_client
    from src.ai_engine.engine import build_chat_client
    from src.interface.main import PersonalAIInterface

    # Try to get engine info from the current configuration
    interface = PersonalAIInterface()
    interface.vault = vault
    interface._load_ollama_config()

    # Determine which engine to use for model listing
    engine = interface.ollama_config.get("engine", "ollama")
    host = interface.ollama_config.get("llm_host",
                                      "http://127.0.0.1:8000" if engine == "omlx" else "http://localhost:11434")

    return build_chat_client(engine=engine, host=host)


@st.dialog("Delete this agent?")
def _delete_agent_dialog(vault: Any, agent_id: str, agent_name: str) -> None:
    st.write(
        f"This permanently deletes the **{agent_name}** agent. "
        "This cannot be undone."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Delete", type="primary", use_container_width=True):
            store.delete_agent(vault, agent_id)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Delete this MCP server?")
def _delete_mcp_server_dialog(vault: Any, server_id: str, server_name: str) -> None:
    st.write(
        f"This deletes the **{server_name}** server definition and its stored "
        "OAuth credentials. Agents that use it lose its tools."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Delete", type="primary", use_container_width=True):
            store.delete_mcp_server(vault, server_id)
            try:
                vault.delete_data(f"mcp_oauth_{server_id}")
            except Exception:
                pass
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Delete this skill?")
def _delete_skill_dialog(slug: str, skill_name: str) -> None:
    st.write(
        f"This permanently deletes the **{skill_name}** skill file. "
        "Agents that attach it simply stop receiving its instructions."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Delete", type="primary", use_container_width=True):
            skills.delete_skill(slug)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# -- Agents tab ---------------------------------------------------------------


def _model_options(clients: Any, current: str) -> List[str]:
    """Installed models, keeping `current` selectable even if it was pulled
    away since the agent was saved."""
    models = clients.list_models() or [DEFAULT_CHAT_MODEL]
    if current and current not in models:
        models = models + [current]
    return models


def _agent_form(
    clients: Any,
    details: Dict[str, Any],
    servers: List[Dict[str, Any]],
    agent: Optional[Dict[str, Any]],
    key_prefix: str,
) -> Dict[str, Any]:
    """The create/edit agent form. Returns the collected fields; the caller
    renders the single Save button and validates/persists."""
    k = lambda name: f"{key_prefix}_{name}"
    base = store.new_agent_defaults()
    agent = agent or base

    name = st.text_input("Name", value=agent.get("name", ""), key=k("name"))
    description = st.text_input(
        "Description", value=agent.get("description", ""), key=k("description")
    )
    icon = st.text_input(
        "Icon (Material Symbol)",
        value=agent.get("icon", ":material/agent_duo:"),
        key=k("icon"),
    )

    capabilities = clients.model_capabilities()
    models = _model_options(clients, agent.get("model") or DEFAULT_CHAT_MODEL)
    model = st.selectbox(
        "Model",
        models,
        index=models.index(agent.get("model") or DEFAULT_CHAT_MODEL),
        format_func=lambda m: f"{m} (tools)" if "tools" in capabilities.get(m, []) else m,
        key=k("model"),
    )

    temp_col, tokens_col = st.columns(2)
    with temp_col:
        temperature = st.slider(
            "Temperature",
            0.0,
            1.0,
            float(agent.get("temperature", 0.3)),
            0.05,
            key=k("temperature"),
        )
    with tokens_col:
        max_tokens = st.number_input(
            "Max tokens",
            min_value=64,
            max_value=32768,
            value=int(agent.get("max_tokens", 2048)),
            step=64,
            key=k("max_tokens"),
        )

    ctx_col, rounds_col = st.columns(2)
    with ctx_col:
        context_window = st.number_input(
            "Context window (0 = model default)",
            min_value=0,
            max_value=1048576,
            value=int(agent.get("context_window_tokens", 0)),
            step=1024,
            key=k("context_window"),
        )
        model_ctx = (details.get(model) or {}).get("context_length")
        if model_ctx:
            st.caption(f"Ollama reports {model_ctx:,} tokens for {model}")
    with rounds_col:
        max_rounds = st.number_input(
            "Max tool rounds (0 = no tools)",
            min_value=0,
            max_value=50,
            value=int(agent.get("max_tool_rounds", DEFAULT_AGENT_MAX_TOOL_ROUNDS)),
            step=1,
            key=k("max_rounds"),
        )

    instructions = st.text_area(
        "Instructions (added to the agent's system prompt)",
        value=agent.get("instructions", ""),
        height=120,
        key=k("instructions"),
    )

    rag_enabled = st.checkbox(
        "Ground answers in your vault (RAG)",
        value=bool(agent.get("rag_enabled", True)),
        key=k("rag_enabled"),
    )
    scope = st.multiselect(
        "RAG scope (empty = all categories)",
        category_keys(),
        default=[
            c
            for c in (agent.get("rag_scope_categories") or [])
            if c in category_keys()
        ],
        format_func=lambda c: get_category(c).label,
        disabled=not rag_enabled,
        key=k("rag_scope"),
    )

    server_options = [s["id"] for s in servers]
    server_labels = {s["id"]: s.get("name", s["id"]) for s in servers}
    server_ids = st.multiselect(
        "MCP tool servers (empty = no tools)",
        server_options,
        default=[sid for sid in (agent.get("mcp_server_ids") or []) if sid in server_options],
        format_func=lambda sid: server_labels.get(sid, sid),
        key=k("servers"),
    )

    skill_list = skills.list_skills()
    skill_labels = {s["slug"]: s["name"] for s in skill_list}
    skill_slugs = st.multiselect(
        "Skills",
        [s["slug"] for s in skill_list],
        default=[slug for slug in (agent.get("skills") or []) if slug in skill_labels],
        format_func=lambda slug: skill_labels.get(slug, slug),
        key=k("skills"),
    )

    require_confirm = st.checkbox(
        "Require confirmation before state-changing tools",
        value=bool(agent.get("require_tool_confirmation", True)),
        key=k("require_confirm"),
    )

    # Warnings the user should see before saving (design doc §9):
    if server_ids and max_rounds > 0 and "tools" not in capabilities.get(model, []):
        st.warning(
            f"Model '{model}' does not support tool calling - this agent will "
            "run without tools."
        )
    remote_attached = [
        s for s in servers if s["id"] in server_ids and s.get("kind") == "remote"
    ]
    if rag_enabled and remote_attached:
        names = ", ".join(s.get("name", "?") for s in remote_attached)
        st.warning(
            f"Data egress: this agent shares your messages and vault context "
            f"(RAG) with remote server(s): {names}. Keep the RAG scope narrow "
            "or turn RAG off for this agent."
        )

    return {
        "name": name.strip(),
        "description": description.strip(),
        "icon": icon.strip() or ":material/agent_duo:",
        "model": model,
        "temperature": temperature,
        "max_tokens": int(max_tokens),
        "context_window_tokens": int(context_window),
        "instructions": instructions,
        "rag_enabled": bool(rag_enabled),
        "rag_scope_categories": list(scope),
        "mcp_server_ids": list(server_ids),
        "skills": list(skill_slugs),
        "max_tool_rounds": int(max_rounds),
        "require_tool_confirmation": bool(require_confirm),
    }


def _render_agents_tab(vault: Any, clients: Any) -> None:
    details = clients.model_details()
    servers = store.list_mcp_servers(vault)
    agents = [store.general_agent()] + store.list_agents(vault)

    editing = st.session_state.get("agents_editing_id")  # "new" or an agent id
    if editing is None:
        if st.button("New Agent", icon=":material/person_add:", type="primary"):
            st.session_state["agents_editing_id"] = "new"
            st.rerun()

    for agent in agents:
        with st.container(border=True):
            head_col, actions_col = st.columns([5, 2])
            with head_col:
                badge = " `built-in`" if agent.get("built_in") else ""
                st.markdown(
                    f"{agent.get('icon', ':material/agent:')} **{agent['name']}**{badge}"
                )
                if agent.get("description"):
                    st.caption(agent["description"])
                attached = [
                    s
                    for s in servers
                    if s["id"] in (agent.get("mcp_server_ids") or [])
                ]
                st.caption(
                    f"Model {agent.get('model', '?')} · "
                    f"{len(attached)} tool server(s) · "
                    f"RAG {'on' if agent.get('rag_enabled') else 'off'} · "
                    f"skills: {len(agent.get('skills') or [])}"
                )
            with actions_col:
                if not agent.get("built_in"):
                    if st.button("Edit", key=f"agent_edit_{agent['id']}"):
                        st.session_state["agents_editing_id"] = agent["id"]
                        st.rerun()
                    if st.button("Delete", icon=":material/delete:", key=f"agent_del_{agent['id']}"):
                        _delete_agent_dialog(vault, agent["id"], agent["name"])

    if editing is not None:
        existing = None
        if editing != "new":
            existing = store.get_agent(vault, editing)
            if existing is None:
                st.session_state.pop("agents_editing_id", None)
                st.rerun()
        title = "New Agent" if existing is None else f"Edit {existing['name']}"
        with st.expander(f":material/edit: {title}", expanded=True):
            fields = _agent_form(clients, details, servers, existing, key_prefix=editing)
            save_col, cancel_col = st.columns([1, 1])
            with save_col:
                label = "Create agent" if existing is None else "Save changes"
                if st.button(label, type="primary", key=f"agent_save_{editing}"):
                    if not fields["name"]:
                        st.error("The agent needs a name.")
                    else:
                        if existing is None:
                            store.create_agent(vault, fields)
                        else:
                            merged = {**existing, **fields}
                            store.update_agent(vault, existing["id"], merged)
                        st.session_state.pop("agents_editing_id", None)
                        st.rerun()
            with cancel_col:
                if st.button("Cancel", key=f"agent_cancel_{editing}"):
                    st.session_state.pop("agents_editing_id", None)
                    st.rerun()


# -- MCP Servers tab -----------------------------------------------------------


def _render_mcp_add_form(vault: Any) -> None:
    name = st.text_input("Name", key="mcp_new_name", placeholder="Robinhood Trading")
    kind = st.radio(
        "Kind", ["remote", "stdio"], horizontal=True, key="mcp_new_kind"
    )
    url = command = args_text = ""
    if kind == "remote":
        url = st.text_input(
            "MCP endpoint URL",
            key="mcp_new_url",
            placeholder="https://agent.robinhood.com/mcp/trading",
        )
    else:
        command = st.text_input(
            "Command", key="mcp_new_command", placeholder="npx"
        )
        args_text = st.text_input(
            "Arguments (space-separated)",
            key="mcp_new_args",
            placeholder="-y @modelcontextprotocol/server-filesystem /path",
        )

    if st.button("Add server", type="primary", key="mcp_new_add"):
        if not name.strip():
            st.error("The server needs a name.")
            return
        if kind == "remote" and not url.strip():
            st.error("A remote server needs an endpoint URL.")
            return
        if kind == "stdio" and not command.strip():
            st.error("A stdio server needs a command.")
            return
        server = {
            "name": name.strip(),
            "kind": kind,
            "enabled": True,
            "last_status": None,
            "last_error": None,
            "tool_count": 0,
        }
        if kind == "remote":
            server["url"] = url.strip()
        else:
            server["command"] = command.strip()
            server["args"] = args_text.split()
        store.save_mcp_server(vault, server)
        st.session_state.pop("mcp_add_open", None)
        st.rerun()


def _render_mcp_server_card(vault: Any, manager: McpManager, server: Dict[str, Any]) -> None:
    server_id = server["id"]
    status = server.get("last_status")
    icon, label = _SERVER_STATUS.get(status, (":material/help:", "Not connected"))
    endpoint = server.get("url") or (
        " ".join([server.get("command", "")] + list(server.get("args") or []))
    )

    with st.container(border=True):
        st.markdown(f"**{server.get('name', server_id)}** · {server.get('kind', '?')}")
        if endpoint:
            st.code(endpoint)
        status_bits = [f"{icon} {label}"]
        if server.get("tool_count"):
            status_bits.append(f"{server['tool_count']} tools")
        if server.get("last_error"):
            status_bits.append(f"last error: {server['last_error']}")
        st.caption(" · ".join(status_bits))

        if manager.has_stored_credentials(server_id):
            st.caption(
                ":material/verified: Credentials are stored in your encrypted "
                "vault - connecting again happens without a browser."
            )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button(
                "Disable" if server.get("enabled", False) else "Enable",
                icon=":material/power_settings_new:",
                use_container_width=True,
                key=f"mcp_toggle_{server_id}",
            ):
                store.save_mcp_server(
                    vault, {**server, "enabled": not server.get("enabled", False)}
                )
                st.rerun()
        with c2:
            if st.button(
                "Connect & Authorize" if server.get("kind") == "remote" else "Test",
                icon=":material/link:",
                use_container_width=True,
                key=f"mcp_auth_{server_id}",
            ):
                if server.get("kind") == "remote" and not manager.has_stored_credentials(
                    server_id
                ):
                    st.caption(
                        f"This opens your browser to sign in. It waits up to "
                        f"{MCP_OAUTH_TIMEOUT_SECONDS}s for you to finish."
                    )
                with st.status(f"Connecting to {server.get('name', server_id)}...", expanded=True):
                    try:
                        manager.connect(server_id, interactive=True)
                    except McpError as exc:
                        st.error(str(exc))
                st.rerun()
        with c3:
            if st.button(
                "Refresh tools",
                icon=":material/sync:",
                use_container_width=True,
                key=f"mcp_refresh_{server_id}",
            ):
                with st.status("Refreshing tools...", expanded=True):
                    try:
                        manager.connect(server_id, interactive=False)
                    except McpError as exc:
                        st.error(str(exc))
                st.rerun()
        with c4:
            if st.button(
                "Delete",
                icon=":material/delete:",
                use_container_width=True,
                key=f"mcp_del_{server_id}",
            ):
                _delete_mcp_server_dialog(vault, server_id, server.get("name", server_id))

        if server.get("enabled", False):
            with st.expander(f":material/construction: Tools ({server.get('tool_count') or '?'})"):
                try:
                    tools = manager.list_tools(server_id)
                except McpError as exc:
                    st.warning(
                        f"Could not list tools: {exc}. Use Connect & Authorize "
                        "or Refresh tools first."
                    )
                    return
                if not tools:
                    st.caption("No tools reported by this server.")
                for tool in tools:
                    badge = (
                        ":material/edit: write" if tool.is_write else ":material/visibility: read"
                    )
                    st.markdown(f"**{tool.name}** — {badge}")
                    if tool.description:
                        st.caption(tool.description)


def _render_mcp_tab(vault: Any) -> None:
    manager = _mcp_manager(vault)
    servers = store.list_mcp_servers(vault)

    add_open = bool(st.session_state.get("mcp_add_open")) or not servers
    if not add_open:
        if st.button("Add server", icon=":material/add:", type="primary"):
            st.session_state["mcp_add_open"] = True
            st.rerun()
    if add_open:
        with st.expander(":material/hub: Add MCP server", expanded=add_open):
            _render_mcp_add_form(vault)
            if st.button("Cancel", key="mcp_add_cancel"):
                st.session_state.pop("mcp_add_open", None)
                st.rerun()

    for server in servers:
        _render_mcp_server_card(vault, manager, server)


# -- Skills tab ------------------------------------------------------------------


def _render_skills_tab() -> None:
    st.caption(
        f"Skills are plaintext instruction files in `{skills.skills_dir()}`. "
        "Attached skills are injected into the agent's system prompt."
    )
    existing = skills.list_skills()

    editing = st.session_state.get("skills_editing_slug")  # "new" or a slug
    if editing is None:
        if st.button("New skill", icon=":material/note_add:", type="primary"):
            st.session_state["skills_editing_slug"] = "new"
            st.rerun()

    if editing is not None:
        skill = None if editing == "new" else skills.read_skill(editing)
        if editing != "new" and skill is None:
            st.session_state.pop("skills_editing_slug", None)
            st.rerun()
        title = "New skill" if skill is None else f"Edit {skill['name']}"
        with st.expander(f":material/edit: {title}", expanded=True):
            name = st.text_input(
                "Name", value=skill["name"] if skill else "", key=f"skill_{editing}_name"
            )
            description = st.text_input(
                "Description (one line)",
                value=skill["description"] if skill else "",
                key=f"skill_{editing}_desc",
            )
            body = st.text_area(
                "Instructions",
                value=skill["body"] if skill else "",
                height=180,
                key=f"skill_{editing}_body",
            )
            save_col, cancel_col = st.columns([1, 1])
            with save_col:
                if st.button("Save skill", type="primary", key=f"skill_{editing}_save"):
                    if not name.strip():
                        st.error("The skill needs a name.")
                    else:
                        skills.write_skill(
                            editing if skill else "", name.strip(), description.strip(), body
                        )
                        st.session_state.pop("skills_editing_slug", None)
                        st.rerun()
            with cancel_col:
                if st.button("Cancel", key=f"skill_{editing}_cancel"):
                    st.session_state.pop("skills_editing_slug", None)
                    st.rerun()

    for skill in existing:
        with st.container(border=True):
            head_col, actions_col = st.columns([5, 1])
            with head_col:
                st.markdown(f":material/note: **{skill['name']}** (`{skill['slug']}`)")
                if skill["description"]:
                    st.caption(skill["description"])
            with actions_col:
                if st.button("Edit", key=f"skill_edit_{skill['slug']}"):
                    st.session_state["skills_editing_slug"] = skill["slug"]
                    st.rerun()
                if st.button(
                    "Delete", icon=":material/delete:", key=f"skill_del_{skill['slug']}"
                ):
                    _delete_skill_dialog(skill["slug"], skill["name"])


# -- Activity tab ------------------------------------------------------------------


def _render_activity_tab(vault: Any) -> None:
    entries = store.list_activity(vault, limit=100)
    if not entries:
        st.info(
            "No tool activity yet. Every agent tool call shows up here with "
            "its arguments, outcome, and duration (newest first)."
        )
        return
    rows = [
        {
            "time": str(e.get("ts", ""))[:19],
            "agent": e.get("agent_name", ""),
            "server": e.get("server", ""),
            "tool": e.get("tool", ""),
            "args": e.get("args_digest", ""),
            "ok": "yes" if e.get("ok") else "no",
            "ms": e.get("duration_ms", 0),
            "error": e.get("error") or "",
        }
        for e in entries
    ]
    st.dataframe(rows, hide_index=True)


# -- page --------------------------------------------------------------------------


def render_agents_page(vault: Any, ollama_client: Optional[Any] = None) -> None:
    """The Agents page: four tabs over the agent/MCP/skills/activity state."""
    st.header(":material/agent: Agents")
    st.caption(
        "Configure agents (model + instructions + tools), the MCP servers they "
        "draw tools from, reusable skills, and a log of every tool call."
    )

    clients = _ollama_client(ollama_client, vault)

    tab_agents, tab_mcp, tab_skills, tab_activity = st.tabs(
        [
            ":material/smart_toy: Agents",
            ":material/hub: MCP Servers",
            ":material/note: Skills",
            ":material/receipt_long: Activity",
        ]
    )
    with tab_agents:
        _render_agents_tab(vault, clients)
    with tab_mcp:
        _render_mcp_tab(vault)
    with tab_skills:
        _render_skills_tab()
    with tab_activity:
        _render_activity_tab(vault)
