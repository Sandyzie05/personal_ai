#!/usr/bin/env python3
"""
Personal AI System - Web Interface

Privacy-first personal assistant with encrypted data storage.
All processing happens locally using open-source AI models.
"""

import streamlit as st
import sys
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

# Load environment variables from .env file if it exists
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ai_engine.chat_engine import ChatEngineError
from src.data_extraction import category_keys, get_category
from src.data_extraction.categories import DEFAULT_CATEGORY_KEY
from src.data_vault import (
    DataVault,
    DataVaultError,
    create_vault,
    is_internal_vault_key,
)
from src.agents import store as agent_store
from src.interface import agents
from src.interface import sync_panel, upload
from src.interface import chat_sessions
from src.interface import dashboard
from src.interface.file_grouping import group_files_by_category
from src.interface.chat import (
    ASSISTANT_AVATAR,
    USER_AVATAR,
    ChatHistory,
    render_chat_history,
    render_sources,
)
from src.interface import theme
from src.security.auth import AuthManager, AuthenticationError
from src.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    PERSONAL_AI_LLM_ENGINE,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_EMBED_MODEL,
    PERSONAL_AI_EMBED_ENGINE
)

# Nav key -> display label (Material Symbols, bundled with Streamlit - no
# external font/network request). Keys are stable identifiers used in run()'s
# routing; labels are what the sidebar radio actually shows.
NAV_PAGES = {
    "chat": ":material/chat: Chat",
    "upload": ":material/upload_file: Upload",
    "files": ":material/folder: Files",
    "dashboard": ":material/dashboard: Dashboard",
    "agents": ":material/agent: Agents",
    "forecast": ":material/query_stats: Forecast",
    "settings": ":material/settings: Settings",
}


@st.dialog("Clear chat history?")
def _clear_chat_dialog(chat_history: ChatHistory) -> None:
    """Confirm before permanently deleting chat history.

    Replaces a prior call to `st.confirm(...)`, which is not a real
    Streamlit API and would have raised AttributeError the first time
    anyone clicked Clear.
    """
    st.write(
        "This permanently deletes all messages in this chat. This cannot be undone."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Clear", type="primary", use_container_width=True):
            chat_history.clear()
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Delete this chat?")
def _delete_session_dialog(app: "PersonalAIInterface", session_id: str) -> None:
    """Confirm before permanently deleting a chat session and its messages.

    Mirrors `_clear_chat_dialog`, but also has to pick (or create) a new
    active session afterwards since the deleted one can no longer be
    displayed.
    """
    st.write(
        "This permanently deletes this chat and all its messages. This cannot be undone."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Delete", type="primary", use_container_width=True):
            chat_sessions.delete_session(app.vault, session_id)
            remaining = chat_sessions.list_sessions(app.vault)
            new_active = (
                remaining[0]["id"]
                if remaining
                else chat_sessions.create_session(app.vault)
            )
            st.session_state["active_chat_session_id"] = new_active
            app._load_chat_history_for_session(new_active)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


class PersonalAIInterface:
    """Main interface for the Personal AI System."""

    def __init__(self):
        self.vault: Optional[DataVault] = None
        self.encryption_key: Optional[bytes] = None
        self.chat_history: Optional[ChatHistory] = None
        # Which chat session `self.chat_history` currently corresponds to -
        # lets render_chat_page() avoid rebuilding (and re-decrypting) the
        # ChatHistory on every rerun unless the active session actually
        # changed.
        self._chat_history_session_id: Optional[str] = None
        self.ollama_config: Dict[str, Any] = {}
        self.chat_engine: Optional[Any] = None
        self._initialized: bool = False
        # Don't initialize heavy components on startup
        # They'll be initialized lazily when needed

    def _get_vault_instance(self, _key_path: str, _key: bytes) -> DataVault:
        """Vault instance to prevent multiple instances during initialization."""
        return create_vault(vault_path=_key_path, encryption_key=_key)

    def _get_chat_engine_instance(self, _key_path: str, _key: bytes) -> Any:
        """ChatEngine instance to prevent multiple instances during initialization."""
        from src.ai_engine.chat_engine import ChatEngine
        from src.ai_engine.engine import build_chat_client, client_kwargs_from_config

        # Load the configuration from the vault
        config_data = None
        if self.vault:
            try:
                config_data = self.vault.retrieve_data("ollama_config")
            except Exception:
                # If we can't load config, use defaults
                config_data = {}

        # Build client parameters from config
        client_kwargs = client_kwargs_from_config(config_data)

        # Remove the engine-related kwargs to match ChatEngine constructor
        # ChatEngine only takes ollama_client, vault_path, encryption_key, model, context_window_tokens, system_prompt, rag_scope_categories
        filtered_kwargs = {
            k: v for k, v in client_kwargs.items()
            if k in ['vault_path', 'encryption_key', 'model', 'context_window_tokens', 'system_prompt', 'rag_scope_categories']
        }

        # If no explicit ollama_client was provided, we'll let ChatEngine create it
        return ChatEngine(
            vault_path=_key_path,
            encryption_key=_key,
            **filtered_kwargs
        )

    def _init_vault(self) -> None:
        """Initialize encrypted data vault using the DEK unlocked at login.

        The DEK is never read from a plaintext key file here - it lives only
        in st.session_state for the duration of the authenticated session,
        having been unwrapped from the password-protected auth file by
        AuthManager during _render_login_gate().
        """
        if self._initialized and self.vault is not None:
            return

        self.encryption_key = st.session_state.get("vault_key")
        if not self.encryption_key:
            # Should never happen: run() gates all rendering behind login.
            st.error("No unlocked vault key in this session. Please log in again.")
            return

        try:
            vault_path = os.path.expanduser("~/.personal_ai_vault")
            self.vault = self._get_vault_instance(vault_path, self.encryption_key)
        except Exception as e:
            st.error(f"Failed to initialize vault: {str(e)}")

        self._initialized = True

    def _ensure_vault_and_rag(self) -> None:
        """Ensure vault and RAG are initialized (lazy initialization)."""
        if not self._initialized:
            self._init_vault()

        # Skip if no encryption key or if already initialized
        if self.encryption_key is None or self.chat_engine is not None:
            return

        try:
            vault_path = os.path.expanduser("~/.personal_ai_vault")
            self.chat_engine = self._get_chat_engine_instance(
                vault_path, self.encryption_key
            )
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")
            # Don't raise - let the system continue with fallback

    def _show_rag_not_ready(self) -> bool:
        """Check if RAG is not ready due to missing model."""
        if not self.encryption_key:
            st.warning(
                "Encryption key not loaded. Please reload the app.",
                icon=":material/lock:",
            )
            return True
        if self.chat_engine is None:
            st.warning(
                "AI model not loaded. This will be initialized on first chat.",
                icon=":material/smart_toy:",
            )
            return True
        return False

    def _initialize_rag(self) -> None:
        """Initialize chat engine with RAG for querying vault data."""
        if self.encryption_key is None:
            return

        try:
            from src.ai_engine.chat_engine import ChatEngine
            from src.ai_engine.engine import build_chat_client, client_kwargs_from_config

            # Load the configuration from the vault
            config_data = None
            if self.vault:
                try:
                    config_data = self.vault.retrieve_data("ollama_config")
                except Exception:
                    # If we can't load config, use defaults
                    config_data = {}

            # Build client parameters from config
            client_kwargs = client_kwargs_from_config(config_data)

            # Remove the engine-related kwargs to match ChatEngine constructor
            # ChatEngine only takes ollama_client, vault_path, encryption_key, model, context_window_tokens, system_prompt, rag_scope_categories
            filtered_kwargs = {
                k: v for k, v in client_kwargs.items()
                if k in ['vault_path', 'encryption_key', 'model', 'context_window_tokens', 'system_prompt', 'rag_scope_categories']
            }

            self.chat_engine = ChatEngine(
                vault_path=os.path.expanduser("~/.personal_ai_vault"),
                encryption_key=self.encryption_key,
                **filtered_kwargs
            )
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")
            raise  # Re-raise to let the caller handle it properly

    def _load_chat_history_for_session(self, session_id: str) -> None:
        """(Re)build `self.chat_history` for `session_id` and remember which
        session it corresponds to, so callers can skip rebuilding it on
        reruns where the active session hasn't changed.
        """
        history_key = chat_sessions.history_key_for(session_id)
        for session in chat_sessions.list_sessions(self.vault):
            if session.get("id") == session_id:
                history_key = session.get("history_key", history_key)
                break
        self.chat_history = ChatHistory(self.vault, history_key=history_key)
        self._chat_history_session_id = session_id

    def _ensure_active_session_and_history(self) -> str:
        """Pick (or create) the active chat session and load its history.

        The active session id lives in `st.session_state` (survives reruns
        within a browser tab, not across app restarts) - the vault-backed
        session index is the durable state. On first use in a session, runs
        the one-time legacy-history migration and defaults to the most
        recently active session (creating a first one if none exist yet).
        """
        active_id = st.session_state.get("active_chat_session_id")
        if not active_id:
            chat_sessions.migrate_legacy_history(self.vault)
            sessions = chat_sessions.list_sessions(self.vault)
            if not sessions:
                chat_sessions.create_session(self.vault)
                sessions = chat_sessions.list_sessions(self.vault)
            active_id = sessions[0]["id"]
            st.session_state["active_chat_session_id"] = active_id

        if self.chat_history is None or self._chat_history_session_id != active_id:
            self._load_chat_history_for_session(active_id)

        return active_id

    def _render_session_controls(self, active_id: str) -> None:
        """Session switcher, agent selector, new-chat button, and delete-active-session control."""
        sessions = chat_sessions.list_sessions(self.vault)
        session_ids = [s["id"] for s in sessions]

        def _label(session: Dict[str, Any]) -> str:
            title = (session.get("title") or "").strip()
            if title:
                return title[:60]
            return f"Untitled chat ({session.get('id', '')[:8]})"

        labels = {s["id"]: _label(s) for s in sessions}

        # Agent selector (design D10: the session remembers its last agent).
        # A stored selection pointing at a deleted agent is reset to General
        # BEFORE the selectbox is built - a value missing from `options`
        # would otherwise raise a StreamlitAPIException on this render.
        agent_list = (
            [agent_store.general_agent(), agent_store.get_agent(self.vault, "builtin-coordinator")]
            + [agent_store.get_agent(self.vault, f"builtin-{r}") for r in ("expense", "forecaster", "market")]
            + agent_store.list_agents(self.vault)
        )
        agent_ids = [a["id"] for a in agent_list]
        agent_names = {a["id"]: a["name"] for a in agent_list}
        if st.session_state.get("active_agent_id") not in agent_ids:
            session = next((s for s in sessions if s.get("id") == active_id), None)
            session_agent = (session or {}).get("agent_id")
            st.session_state["active_agent_id"] = (
                session_agent
                if session_agent in agent_ids
                else agent_store.GENERAL_AGENT_ID
            )

        select_col, agent_col, new_col, delete_col = st.columns([3, 2, 1, 1])
        with select_col:
            if session_ids:
                current_index = (
                    session_ids.index(active_id) if active_id in session_ids else 0
                )
                chosen = st.selectbox(
                    "Chat session",
                    session_ids,
                    index=current_index,
                    format_func=lambda sid: labels.get(sid, sid[:8]),
                    label_visibility="collapsed",
                )
                if chosen != active_id:
                    st.session_state["active_chat_session_id"] = chosen
                    self._load_chat_history_for_session(chosen)
                    st.rerun()
        with agent_col:
            st.selectbox(
                "Agent",
                agent_ids,
                index=agent_ids.index(st.session_state["active_agent_id"]),
                format_func=lambda aid: agent_names.get(aid, aid),
                label_visibility="collapsed",
                key="active_agent_id",
            )
        with new_col:
            if st.button("New Chat", icon=":material/add:", use_container_width=True):
                new_id = chat_sessions.create_session(self.vault)
                st.session_state["active_chat_session_id"] = new_id
                self._load_chat_history_for_session(new_id)
                st.rerun()
        with delete_col:
            if st.button(
                "Delete chat",
                icon=":material/delete:",
                use_container_width=True,
                disabled=not session_ids,
            ):
                _delete_session_dialog(self, active_id)

    def _respond(
        self, user_query: str, history: Optional[List[Dict[str, str]]] = None
    ) -> tuple[str, list[str]]:
        """Render the assistant's answer inside the caller's st.chat_message block.

        Streams token-by-token through the RAG chat engine when available
        (so the reply appears incrementally instead of after a multi-second
        blocking wait); falls back to a single non-streaming Ollama call
        with no vault-grounded retrieval if the chat engine couldn't be
        initialized (e.g. Ollama was unreachable at startup).

        `history` is the chronological (oldest-first) prior conversation,
        NOT including `user_query` itself - threaded through so the model
        sees prior turns instead of treating every question in isolation.
        """
        if self.chat_engine:
            try:
                stream = self.chat_engine.query_vault_stream(
                    user_query, use_rag=True, history=history
                )
                response_text = st.write_stream(stream)
                return response_text, stream.sources
            except ChatEngineError as e:
                error_text = str(e)
                st.error(error_text, icon=":material/warning:")
                # Try fallback with clear engine status
                st.info("Falling back to basic chat mode...")
                with st.spinner("Thinking..."):
                    response_text, sources = self._get_fallback_response(user_query)
                st.markdown(response_text)
                return response_text, sources
            except Exception as e:
                # General fallback for any chat engine errors
                st.warning(f"Chat engine error: {str(e)}. Falling back to basic mode.")
                with st.spinner("Thinking..."):
                    response_text, sources = self._get_fallback_response(user_query)
                st.markdown(response_text)
                return response_text, sources

        # If no chat engine, use fallback directly
        with st.spinner("Thinking..."):
            response_text, sources = self._get_fallback_response(user_query)
        st.markdown(response_text)
        return response_text, sources

    def _get_fallback_response(self, user_query: str) -> tuple[str, list[str]]:
        """Non-streaming fallback used only when the RAG chat engine is unavailable."""
        if not self.ollama_config:
            self._load_ollama_config()

        try:
            from src.ai_engine.engine import build_chat_client

            # Determine which engine to use for fallback
            from src.ai_engine.engine import client_kwargs_from_config
            kw = client_kwargs_from_config(self.ollama_config)
            engine, host = kw["engine"], kw["host"]
            client = build_chat_client(engine=engine, host=host, model=kw["model"])

            # Reuse the exact same grounding-first system prompt as the RAG path
            # so a fallback answer can't silently drift into "answer from your own
            # knowledge". The fallback has no retrieved context, so the prompt
            # also tells it to say "I haven't indexed your data yet" rather than
            # inventing an answer.
            from src.ai_engine.chat_engine import ChatEngine

            messages = [
                {
                    "role": "system",
                    "content": f"{ChatEngine.SYSTEM_PROMPT} "
                    "No retrieved context is attached to this turn because the "
                    "search index isn't loaded yet; if you don't actually know "
                    "the answer from the user's data, say so instead of guessing.",
                }
            ]

            if self.chat_history:
                for msg in self.chat_history.get_recent_messages(5):
                    messages.append({"role": msg.role, "content": msg.content})

            messages.append({"role": "user", "content": user_query})

            response = client.chat(messages=messages, stream=False)

            content = response["message"]["content"]

            sources = self._generate_sources(user_query)

            return content, sources

        except Exception as e:
            # Declare engine in local scope to avoid UnboundLocalError
            from src.ai_engine.engine import client_kwargs_from_config
            kw = client_kwargs_from_config(self.ollama_config)
            engine, host = kw["engine"], kw["host"] or PERSONAL_AI_OMLX_HOST
            return (
                f"Error getting response: {str(e)}. Make sure {engine} server is running at {host}",
                [],
            )

    def _generate_sources(self, query: str) -> list[str]:
        """Generate source citations based on query."""
        sources = []

        vault_keys = self.vault.list_keys() if self.vault else []

        if vault_keys:
            sources.append(
                f":material/folder: Querying {len(vault_keys)} local data files"
            )
        else:
            sources.append(
                ":material/info: No local data files found. Upload documents "
                "to enable data-driven answers."
            )

        return sources

    def _agent_runtime(self) -> Any:
        """Session-cached AgentRuntime (Streamlit reruns the whole script).

        Rebuilt when the vault instance changes (lock/unlock cycle). The
        runtime owns its McpManager - no connection caching inside (each
        MCP operation opens a fresh transport; see src/agents/mcp.py).
        """
        from src.agents.mcp import McpManager
        from src.agents.orchestration.skill_pack import install_builtin_skills
        from src.agents.runtime import AgentRuntime

        install_builtin_skills()

        cached = st.session_state.get("agent_runtime")
        if cached is None or getattr(cached, "vault", None) is not self.vault:
            cached = AgentRuntime(self.vault, mcp=McpManager(self.vault))
            st.session_state["agent_runtime"] = cached
        return cached

    def _respond_coordinated(self, user_query: str, active_id: str, agent: Dict[str, Any]) -> None:
        """Coordinator turn: plan -> parallel specialists -> deterministic merge -> escalation."""
        from src.agents.orchestration import service
        from src.agents.orchestration.workers import render_report
        from src.interface.forecast import _scenario_factory

        try:
            coordinator = service.build_coordinator(
                self.vault, self.chat_engine.get_structured_records, _scenario_factory(self.vault),
                mcp=self._agent_runtime().mcp, quarantine_client=self.chat_engine.ollama_client)
            with st.status("Coordinator delegating to specialists...", expanded=True):
                report = service.run_query(
                    coordinator, user_query, only=agent.get("role") if agent.get("id") != "builtin-coordinator" else None,
                    planner_client=self.chat_engine.ollama_client)
            service.propose_from_report(self.vault, report)
            answer = render_report(report)
            answer += "\n\n_Parallel specialists: " + ("overlapped" if report.parallel_overlap else "single step") + f", {report.wall_s:.1f}s._"
        except ValueError as exc:  # refusals, nothing delegable
            answer = str(exc)
        except Exception:  # never surface a traceback (or internal detail) from an agent turn
            answer = "The coordinator could not complete this request. Nothing was changed."
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(answer)
        self.chat_history.add_message("user", user_query)
        self.chat_history.add_message("assistant", answer, [])
        chat_sessions.touch_session(self.vault, active_id, title=user_query.strip()[:40], agent_id=agent.get("id"))
        st.rerun()

    def _respond_with_agent(
        self,
        user_query: str,
        history: List[Dict[str, str]],
        active_id: str,
        agent: Dict[str, Any],
    ) -> None:
        """Run one user turn through the agent tool loop (non-General agents).

        The user message is persisted as soon as the run is off the ground
        (so it survives the confirmation pause); the assistant answer lands
        in the history when the run reaches a terminal status.
        """
        if agent.get("id") == "builtin-coordinator" or agent.get("role") in ("expense", "forecaster", "market"):
            return self._respond_coordinated(user_query, active_id, agent)
        runtime = self._agent_runtime()
        with st.status(f"{agent.get('name', 'Agent')} is working...", expanded=True):
            result = runtime.start_run(active_id, agent, user_query, history)

        if result.status == "awaiting_confirmation":
            self.chat_history.add_message("user", user_query)
            chat_sessions.touch_session(
                self.vault, active_id, title=user_query.strip()[:40],
                agent_id=agent.get("id"),
            )
            st.rerun()
            return

        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(result.answer)
            render_sources(result.sources)

        self.chat_history.add_message("user", user_query)
        self.chat_history.add_message("assistant", result.answer, result.sources)
        chat_sessions.touch_session(
            self.vault, active_id, title=user_query.strip()[:40],
            agent_id=agent.get("id"),
        )
        st.rerun()

    def _render_confirmation_card(self, active_id: str, agent: Dict[str, Any]) -> None:
        """The paused run's pending tool call: tool, args, Allow/Deny/Abort.

        Rendered while `agent_run_<active_id>` exists for the active agent.
        A paused run's state lives in the vault (not session_state) so it
        survives reruns, tab switches, and app restarts.
        """
        state = agent_store.load_run_state(self.vault, active_id)
        if state is None:
            return
        pending = state.get("pending") or {}
        server = agent_store.get_mcp_server(self.vault, pending.get("server", ""))
        server_name = (
            server.get("name", pending.get("server", "")) if server else pending.get("server", "")
        )
        agent_name = agent.get("name", "Agent") if agent else state.get("agent_name", "Agent")

        with st.container(border=True):
            st.markdown(
                f":material/pause_circle: **{agent_name}** is waiting for your approval"
            )
            st.write(
                f"About to call **{pending.get('tool')}** on server "
                f"**{server_name}** with:"
            )
            st.json(pending.get("args") or {}, expanded=False)
            trail = state.get("trail") or []
            if trail:
                done = ", ".join(
                    f"{t.get('tool')} ({'ok' if t.get('ok') else 'error'})"
                    for t in trail
                )
                st.caption(f"Already done this turn: {done}")
            st.caption(
                "Allow executes the call now. Deny tells the model it must not "
                "retry. Abort stops the run entirely (the question stays in "
                "the chat without an answer)."
            )
            allow_col, deny_col, abort_col = st.columns(3)
            with allow_col:
                if st.button(
                    "Allow", type="primary", use_container_width=True,
                    key="agent_confirm_allow",
                ):
                    self._resolve_paused_run(active_id, agent, "allow")
            with deny_col:
                if st.button(
                    "Deny", use_container_width=True, key="agent_confirm_deny"
                ):
                    self._resolve_paused_run(active_id, agent, "deny")
            with abort_col:
                if st.button(
                    "Abort", icon=":material/stop:", use_container_width=True,
                    key="agent_confirm_abort",
                ):
                    self._resolve_paused_run(active_id, agent, "abort")

    def _resolve_paused_run(
        self, active_id: str, agent: Optional[Dict[str, Any]], decision: str
    ) -> None:
        """Apply the user's decision to a paused run and render the outcome.

        `agent` may be a stale/None reference (the agent was deleted while
        the run was paused) - the runtime only needs its id/name/server list
        from it, so fall back to a minimal record from the persisted state.
        """
        state = agent_store.load_run_state(self.vault, active_id) or {}
        if agent is None or agent.get("id") != state.get("agent_id"):
            agent = {
                "id": state.get("agent_id", ""),
                "name": state.get("agent_name", "Agent"),
                "mcp_server_ids": [],
                "max_tool_rounds": 0,
                "require_tool_confirmation": False,
            }

        runtime = self._agent_runtime()
        with st.status(f"Resuming run ({decision})...", expanded=True):
            result = runtime.resume_run(active_id, agent, decision)

        if result.status == "awaiting_confirmation":
            # The model immediately requested another sensitive tool.
            st.rerun()
            return

        if result.status in ("complete", "exhausted"):
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                st.markdown(result.answer)
                render_sources(result.sources)
            self.chat_history.add_message(
                "assistant", result.answer, result.sources
            )
        chat_sessions.touch_session(
            self.vault, active_id, agent_id=agent.get("id")
        )
        st.rerun()

    def _load_ollama_config(self) -> None:
        """Load Ollama configuration from vault or use defaults.

        Falls back to defaults on DataVaultError (e.g. this entry was
        encrypted under a different vault key than the one unlocked now)
        instead of crashing the whole page.
        """
        config_data = None
        if self.vault:
            try:
                config_data = self.vault.retrieve_data("ollama_config")
            except DataVaultError as e:
                st.warning(
                    f"Could not load saved settings ({e}). Using defaults.",
                    icon=":material/warning:",
                )

        if config_data:
            self.ollama_config = config_data
        else:
            # Ensure we're always defaulting to correct engine names
            # This fixes issues where old config keys were causing incorrect model loads
            self.ollama_config = {
                "engine": PERSONAL_AI_LLM_ENGINE,  # This will be "ollama" by default
                "llm_host": PERSONAL_AI_OMLX_HOST,
                "llm_model": DEFAULT_CHAT_MODEL,
                "embed_engine": PERSONAL_AI_EMBED_ENGINE,
                "embed_host": PERSONAL_AI_OMLX_HOST,
                "embed_model": PERSONAL_AI_OMLX_EMBED_MODEL,
                "temperature": 0.7,
                "context_window_tokens": 8192,
                "max_tokens": 2048,
                "stream": True,
            }

    def render_sidebar(self) -> str:
        """Render sidebar with navigation and settings. Returns the nav key
        ("chat", "upload", "files", "dashboard", or "settings")."""
        with st.sidebar:
            st.title(":material/lock: Private AI")
            st.caption("Local, encrypted assistant")

            theme.render_theme_toggle()

            st.divider()

            page = st.radio(
                "Navigation",
                list(NAV_PAGES.keys()),
                format_func=lambda key: NAV_PAGES[key],
                label_visibility="collapsed",
            )

            st.divider()

            # At-a-glance vault state: one metric + one privacy line, instead
            # of the old always-100% progress bar and loose captions.
            if self.vault:
                keys = self.vault.list_keys()
                file_keys = [k for k in keys if not is_internal_vault_key(k)]
                st.metric("Documents in vault", len(file_keys))

            vault_state = "Unlocked" if self.encryption_key else "Locked"
            st.caption(
                f":material/lock: Vault **{vault_state}** · encrypted at rest. "
                "Forecast requests public market data; remote agent tools are opt-in."
            )

            # Diagnostics are tucked into an expander so the sidebar stays
            # calm; the button bodies are unchanged, just relocated.
            with st.expander(":material/monitor_heart: Status & diagnostics"):
                if st.button(
                    "Encryption key status",
                    icon=":material/key:",
                    use_container_width=True,
                ):
                    if self.encryption_key:
                        st.success(
                            "Vault key unlocked for this session",
                            icon=":material/check_circle:",
                        )
                    else:
                        st.warning(
                            "No encryption key unlocked", icon=":material/warning:"
                        )

                if st.button(
                    "Vault status",
                    icon=":material/inventory_2:",
                    use_container_width=True,
                ):
                    if self.vault:
                        keys = self.vault.list_keys()
                        st.info(f"{len(keys)} encrypted entries")
                        for key in keys:
                            try:
                                data = self.vault.retrieve_data(key)
                            except DataVaultError as e:
                                st.caption(
                                    f":material/warning: {key}: could not decrypt ({e})"
                                )
                                continue
                            if data and isinstance(data, dict):
                                metadata = data.get("metadata", {})
                                if metadata:
                                    stamp = str(metadata.get("upload_timestamp", ""))[
                                        :10
                                    ]
                                    st.caption(
                                        f":material/description: {metadata.get('original_filename', key)}"
                                        f" · {metadata.get('file_type', '?')}"
                                        f" · {stamp or '?'}"
                                    )

                if st.button(
                    "Check Engine", icon=":material/sync:", use_container_width=True
                ):
                    try:
                        from src.ai_engine.engine import build_chat_client

                        # Determine which engine to check
                        from src.ai_engine.engine import client_kwargs_from_config
                        kw = client_kwargs_from_config(self.ollama_config)
                        engine = kw["engine"]
                        client = build_chat_client(engine=engine, host=kw["host"])

                        if client.check_connection():
                            models = client.list_models()
                            st.success(
                                f"{engine.title()} connected! {len(models)} models available",
                                icon=":material/check_circle:",
                            )
                            for model in models[:5]:
                                st.caption(f"• {model}")
                        else:
                            st.error(
                                f"Cannot connect to {engine} server",
                                icon=":material/error:",
                            )
                    except Exception as e:
                        st.error(
                            f"Cannot connect to {engine} server: {str(e)}",
                            icon=":material/error:",
                        )

            st.divider()

            if st.button(
                "Lock vault", icon=":material/lock_open:", use_container_width=True
            ):
                st.session_state.pop("vault_key", None)
                self.vault = None
                self.chat_engine = None
                self._initialized = False
                st.rerun()

            return page

    def render_chat_page(self) -> None:
        """Render chat interface."""
        self._ensure_vault_and_rag()

        active_id = self._ensure_active_session_and_history()

        title_col, clear_col = st.columns([5, 1])
        with title_col:
            st.header(":material/chat: Chat with Your Data")
            st.caption("All interactions are encrypted and stored locally")
        with clear_col:
            has_messages = bool(self.chat_history.get_messages())
            if st.button(
                "Clear",
                icon=":material/delete_sweep:",
                use_container_width=True,
                disabled=not has_messages,
            ):
                _clear_chat_dialog(self.chat_history)

        self._render_session_controls(active_id)

        # Recomputed fresh on every render (not just after sending a
        # message) so the meter is accurate immediately on page load too.
        history_for_engine = [
            {"role": m.role, "content": m.content}
            for m in self.chat_history.get_messages()
        ]

        if self.chat_engine is not None:
            usage = self.chat_engine.estimate_usage(history_for_engine)
            st.progress(
                min(usage["ratio"], 1.0),
                text=(
                    f"Context used: {usage['ratio'] * 100:.0f}% "
                    f"({usage['used_tokens']:,} / {usage['budget_tokens']:,} tokens)"
                ),
            )
            if usage["ratio"] >= 0.9:
                st.error(
                    "Context window is nearly full — start a new chat to keep responses accurate."
                )
            elif usage["ratio"] >= 0.75:
                st.warning(
                    "Context window is getting full — consider starting a new chat soon."
                )

        with st.expander(":material/tune: Advanced"):
            stale = self.chat_engine.rag_index_stale() if getattr(self, "chat_engine", None) else None
            if stale:
                st.warning(f"Search index is stale ({stale}). Rebuild it.")
            if st.button("Rebuild RAG Index", icon=":material/sync:"):
                # Show progress tracking
                status_placeholder = st.empty()
                status_placeholder.info("Starting RAG index rebuild...")

                try:
                    # Initialize vault first to make sure we have access
                    self._init_vault()
                    status_placeholder.info("Vault initialized successfully")

                    # Check if we have documents to process
                    if self.vault:
                        vault_keys = self.vault.list_keys()
                        file_keys = [k for k in vault_keys if not is_internal_vault_key(k)]
                        status_placeholder.info(f"Found {len(file_keys)} documents to index")

                        if file_keys:
                            self._initialize_rag()
                            with st.spinner("Re-embedding documents with the current embedding model..."):
                                count = self.chat_engine.rebuild_rag_index()
                            status_placeholder.success("✅ RAG index rebuilt")
                            st.success(f"Re-indexed {count} documents")
                        else:
                            status_placeholder.info("ℹ️ No documents to process - index is up to date")
                    else:
                        status_placeholder.warning("⚠️ Vault not accessible")

                except Exception as e:
                    status_placeholder.error(f"❌ Failed to rebuild RAG index: {str(e)}")
                    st.exception(e)
                    raise  # Re-raise to make it clear the process failed
                finally:
                    # Force a rerun to update the UI
                    st.rerun()

        render_chat_history(self.chat_history)

        # A paused agent run (waiting on the confirmation gate) blocks this
        # chat until resolved: the confirmation card is rendered in its place
        # and the input is disabled, so a new message can't silently clobber
        # the persisted in-flight state (agent_run_<session_id>).
        active_agent_id = st.session_state.get(
            "active_agent_id", agent_store.GENERAL_AGENT_ID
        )
        active_agent = agent_store.get_agent(self.vault, active_agent_id)
        paused = agent_store.load_run_state(self.vault, active_id)
        if paused is not None and paused.get("agent_id") == active_agent_id:
            self._render_confirmation_card(active_id, active_agent)
        elif paused is not None:
            st.info(
                f":material/pause_circle: Agent "
                f"'{paused.get('agent_name', 'another agent')}' has an action "
                "waiting for approval in this chat. Switch back to that agent "
                "to allow, deny, or abort it."
            )

        user_input = st.chat_input(
            "Ask a question about your data...", disabled=paused is not None
        )
        if user_input:
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(user_input)

            if active_agent is None or (
                active_agent.get("id") == agent_store.GENERAL_AGENT_ID
            ):
                # The General agent keeps the historical streaming path.
                with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                    response_text, sources = self._respond(
                        user_input, history_for_engine
                    )
                    render_sources(sources)

                self.chat_history.add_exchange(user_input, response_text, sources)
                chat_sessions.touch_session(
                    self.vault, active_id, title=user_input.strip()[:40]
                )
                st.rerun()
            else:
                self._respond_with_agent(
                    user_input, history_for_engine, active_id, active_agent
                )

    def render_upload_page(self) -> None:
        """Render file upload page: category detection + structured extraction."""
        self._ensure_vault_and_rag()
        ollama_client = self.chat_engine.ollama_client if self.chat_engine else None
        upload.render_upload_page(
            vault=self.vault,
            encryption_key=self.encryption_key,
            ollama_client=ollama_client,
            on_uploaded=self._on_document_uploaded,
        )
        sync_panel.render(self.vault, self._on_document_uploaded)

    def _on_document_uploaded(self, storage_key: str, data: Dict[str, Any]) -> None:
        """Index a newly-uploaded document into RAG so it's searchable immediately.

        No UI feedback here - the upload page's `st.status` block already
        reports per-file progress and a final summary.
        """
        if self.chat_engine:
            self.chat_engine.index_document(storage_key, data)

    def render_config_page(self) -> None:
        """Render configuration page."""
        st.header(":material/settings: Settings")
        st.caption(
            "Connect your local Ollama instance and tune generation. "
            "Saved to your encrypted vault — never sent anywhere else."
        )

        from src.interface.config import render_config_page

        self._init_vault()  # Initialize vault but not RAG
        self._load_ollama_config()  # Prefill the form with saved values

        self.ollama_config = render_config_page(self.ollama_config)

        if st.button("Save settings", icon=":material/save:", type="primary"):
            if self.vault:
                self.vault.store_data("ollama_config", self.ollama_config)
                st.success(
                    "Settings saved to your encrypted vault",
                    icon=":material/check_circle:",
                )
            else:
                st.error(
                    "Vault not available — cannot save settings.",
                    icon=":material/error:",
                )

    def render_files_page(self) -> None:
        """Render uploaded files grouped into folder-like sections by category."""
        st.header(":material/folder: Your Uploaded Files")
        st.caption("All files are encrypted and stored locally in your vault")

        self._init_vault()  # Initialize vault but not RAG

        if not self.vault:
            st.warning("Vault not initialized. Please reload the page.")
            return

        keys = self.vault.list_keys()
        file_keys = [k for k in keys if not is_internal_vault_key(k)]

        if not file_keys:
            st.info("No files uploaded yet. Go to the Upload page to add documents.")
            return

        st.success(f"Found {len(file_keys)} uploaded file(s)")

        query = (
            st.text_input(
                "Search files by name",
                icon=":material/search:",
                key="files_search",
                placeholder="Type to filter…",
            )
            .strip()
            .lower()
        )

        # Load each file once, keeping the full record for rendering and just
        # the metadata for grouping.
        records: Dict[str, Dict[str, Any]] = {}
        file_entries = []
        unreadable: List[tuple] = []
        for key in file_keys:
            try:
                data = self.vault.retrieve_data(key)
            except Exception as e:
                # Most commonly a vault entry encrypted under a password/key
                # that's no longer the one unlocked (see docs/security_design.md's
                # "Known limitation: no migration path across a vault key
                # change") - permanently undecryptable, not a transient
                # error. Surface it separately with a way to delete it,
                # rather than a bare error the user can't act on.
                unreadable.append((key, str(e)))
                continue

            if not data or not isinstance(data, dict):
                continue

            records[key] = data
            file_entries.append((key, data.get("metadata", {})))

        if query:
            file_entries = [
                (key, metadata)
                for key, metadata in file_entries
                if query in str(metadata.get("original_filename", key)).lower()
            ]

        groups = group_files_by_category(file_entries)

        if not groups:
            st.info("No files match your search.")
            return

        # Show one category at a time instead of dumping every file into a
        # single long scroll. A segmented control lets the user jump between
        # categories; file cards stay collapsed so the page opens compact and
        # stays shallow (mobile-friendly - no nested expanders).
        labels = [f"{label} ({len(entries)})" for _, label, entries in groups]
        selected = st.segmented_control(
            "Category",
            labels,
            default=labels[0],
            key="files_category",
            label_visibility="collapsed",
        )
        if selected is None:
            selected = labels[0]
        chosen = groups[labels.index(selected)]
        for key, metadata in chosen[2]:
            self._render_file_card(key, records[key], metadata)

        if unreadable:
            with st.expander(
                f":material/warning: {len(unreadable)} file(s) could not be decrypted",
                expanded=False,
            ):
                st.caption(
                    "Usually means these were encrypted under a previous vault "
                    "password/key. They can't be recovered without that old key - "
                    "delete them to clean up the vault."
                )
                for key, error in unreadable:
                    st.text(f"{key}: {error}")
                    if st.button(
                        "Delete",
                        icon=":material/delete:",
                        key=f"delete_unreadable_{key}",
                    ):
                        self.vault.delete_data(key)
                        st.rerun()

    def render_dashboard_page(self) -> None:
        """Render the Dashboard page: customizable at-a-glance widgets."""
        self._init_vault()  # Initialize vault but not RAG

        if not self.vault:
            st.warning("Vault not initialized. Please reload the page.")
            return

        dashboard.render_dashboard_page(self.vault)

    def _run_scheduled(self) -> List[str]:
        from src.agents.orchestration import service
        from src.interface.forecast import _scenario_factory

        self._init_vault()
        if not (self.vault and self.chat_engine):
            return []
        return service.run_scheduled(self.vault, self.chat_engine.get_structured_records, _scenario_factory(self.vault),
                                     mcp=self._agent_runtime().mcp, quarantine_client=self.chat_engine.ollama_client)

    def render_agents_page(self) -> None:
        """Render the Agents page (agents, MCP servers, skills, activity)."""
        self._init_vault()  # Initialize vault but not RAG

        if not self.vault:
            st.warning("Vault not initialized. Please reload the page.")
            return

        ollama_client = self.chat_engine.ollama_client if self.chat_engine else None
        agents.render_agents_page(self.vault, ollama_client, self._run_scheduled)

    def render_forecast_page(self) -> None:
        from src.interface.forecast import render_forecast_page

        self._init_vault()
        if not self.vault:
            st.warning("Vault not initialized. Please reload the page.")
            return
        render_forecast_page(self.vault)

    def _render_file_card(
        self, key: str, data: Dict[str, Any], metadata: Dict[str, Any]
    ) -> None:
        """Render one file's metrics, extraction summary, preview, and controls."""
        try:
            original_filename = metadata.get("original_filename", key)
            file_type = metadata.get("file_type", "unknown")
            file_size = metadata.get("file_size", 0)
            upload_timestamp = metadata.get("upload_timestamp", "unknown")

            # Calculate human-readable file size
            if file_size > 1024 * 1024:
                size_str = f"{file_size / (1024 * 1024):.2f} MB"
            elif file_size > 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size} bytes"

            with st.expander(
                f":material/description: {original_filename}", expanded=False
            ):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Type", file_type.upper())
                with col2:
                    st.metric("Size", size_str)
                with col3:
                    st.metric(
                        "Uploaded",
                        upload_timestamp.split("T")[0]
                        if "T" in upload_timestamp
                        else upload_timestamp,
                    )

                category_key = metadata.get("category")
                if category_key:
                    st.caption(f"Category: {get_category(category_key).label}")

                extraction = data.get("extraction")
                if extraction:
                    total = extraction.get("total")
                    period = f"{extraction.get('period_start', '?')} to {extraction.get('period_end', '?')}"
                    st.success(
                        f"Total: {total} ({period})",
                        icon=":material/payments:",
                    )
                    line_items = extraction.get("line_items", [])
                    if line_items:
                        with st.expander(
                            f":material/receipt_long: {len(line_items)} line item(s)"
                        ):
                            for item in line_items:
                                st.caption(f"{item.get('label')}: {item.get('amount')}")

                st.divider()

                if data.get("text_content"):
                    text_length = len(data["text_content"])
                    st.info(
                        f"Text content: {text_length} characters",
                        icon=":material/article:",
                    )

                    # Show preview if not too long
                    if text_length <= 2000:
                        with st.expander(":material/visibility: View Text Preview"):
                            st.text(data["text_content"][:2000])
                            if text_length > 2000:
                                st.text(f"... ({text_length - 2000} more characters)")

                st.divider()

                all_category_keys = category_keys()
                current_category = category_key or DEFAULT_CATEGORY_KEY
                if current_category not in all_category_keys:
                    current_category = DEFAULT_CATEGORY_KEY
                recat_col, save_col = st.columns([3, 1])
                with recat_col:
                    new_category = st.selectbox(
                        "Re-categorize",
                        all_category_keys,
                        index=all_category_keys.index(current_category),
                        format_func=lambda k: get_category(k).label,
                        key=f"recat_select_{key}",
                    )
                with save_col:
                    st.write("")
                    if st.button(
                        "Save category", icon=":material/save:", key=f"recat_{key}"
                    ):
                        data.setdefault("metadata", {})["category"] = new_category
                        self.vault.store_data(key, data)
                        st.success(
                            f"Moved to {get_category(new_category).label}",
                            icon=":material/check_circle:",
                        )
                        st.rerun()

                if st.button("Delete", icon=":material/delete:", key=f"delete_{key}"):
                    self.vault.delete_data(key)
                    st.rerun()

        except Exception as e:
            st.error(f"Error loading file {key}: {str(e)}")

    def _setup_page(self) -> None:
        """Configure page settings (must be called first)."""
        # Initialize vault first
        try:
            self._init_vault()
        except Exception as e:
            st.error(f"Vault initialization failed: {str(e)}")

        st.set_page_config(
            page_title="Private AI Assistant",
            page_icon="🔒",
            layout="wide",
            initial_sidebar_state="expanded",
        )

        # Hide Streamlit chrome and apply a light, cohesive coat of paint:
        # calmer spacing, rounded controls, and metric values that read as
        # cards (matching the dashboard's bordered containers). Purely
        # cosmetic - no behavior depends on it.
        custom_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header[data-testid="stHeader"] {background: transparent;}
            .block-container {padding-top: 2.75rem; padding-bottom: 3rem;}
            .stButton > button {border-radius: 8px; font-weight: 500;}
            div[data-testid="stMetric"] {
                background: #f4f6f9;
                border: 1px solid #e6e8ec;
                border-radius: 10px;
                padding: 12px 16px;
            }
            section[data-testid="stSidebar"] div[data-testid="stMetric"] {
                background: #ffffff;
            }
            div[data-testid="stExpander"] details {
                border-radius: 10px;
                border-color: #e6e8ec;
            }
            </style>
        """
        st.markdown(custom_style, unsafe_allow_html=True)

    def _render_login_gate(self) -> None:
        """Render registration/login screen; unlocks the vault key on success.

        Nothing else in the app renders until st.session_state['vault_key']
        is populated by a successful register() or authenticate() call.
        """
        st.set_page_config(
            page_title="Private AI Assistant - Sign In", layout="centered"
        )
        st.title(":material/lock: Private AI Assistant")

        auth = AuthManager()

        if not auth.is_registered():
            st.subheader("Create your vault password")
            st.caption(
                "This password protects your encrypted vault. It is never sent "
                "anywhere and cannot be recovered if lost - choose something you "
                "will remember."
            )
            with st.form("register_form"):
                password = st.text_input("New password", type="password")
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create vault")
            if submitted:
                if password != confirm:
                    st.error("Passwords do not match.")
                else:
                    try:
                        dek = auth.register(password)
                        st.session_state["vault_key"] = dek
                        st.rerun()
                    except AuthenticationError as e:
                        st.error(str(e))
        else:
            st.subheader("Enter your vault password")
            with st.form("login_form"):
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Unlock")
            if submitted:
                try:
                    dek = auth.authenticate(password)
                    st.session_state["vault_key"] = dek
                    st.rerun()
                except AuthenticationError as e:
                    st.error(str(e))

    def run(self) -> None:
        """Run the main interface."""
        if "vault_key" not in st.session_state:
            self._render_login_gate()
            return

        self._setup_page()

        page = self.render_sidebar()

        # Repaint dark-mode surfaces here, *after* the sidebar toggles the
        # checkbox. apply_app_shell() reads the toggle's value, so it must run
        # in the same pass the checkbox is constructed - painting it earlier (in
        # _setup_page) would one run behind and never show the freshly picked
        # dark mode. The dark overrides carry !important and follow the light
        # paint in _setup_page, so they win by cascade order; light mode is a
        # no-op, so toggling back off cleanly returns to the light paint.
        theme.apply_app_shell()

        if not st.session_state.get("_schedule_ran"):  # due scheduled jobs run once per session, at app open
            st.session_state["_schedule_ran"] = True
            try:
                self._run_scheduled()
            except Exception:
                pass  # a failing job must never block the app

        if page == "chat":
            self.render_chat_page()
        elif page == "upload":
            self.render_upload_page()
        elif page == "files":
            self.render_files_page()
        elif page == "dashboard":
            self.render_dashboard_page()
        elif page == "agents":
            self.render_agents_page()
        elif page == "forecast":
            self.render_forecast_page()
        elif page == "settings":
            self.render_config_page()


def get_app() -> PersonalAIInterface:
    """Get (or create) the PersonalAIInterface for this browser session.

    Streamlit reruns this whole module on every interaction. Without
    caching the instance in session_state, a brand-new PersonalAIInterface
    was constructed on every rerun - re-initializing the vault and chat
    engine, and re-decrypting the full chat history from disk, on every
    single button press or chat message instead of once per session.
    """
    if "app" not in st.session_state:
        st.session_state["app"] = PersonalAIInterface()
    return st.session_state["app"]


if __name__ == "__main__":
    get_app().run()
