"""AgentRuntime: the tool-using loop that runs a non-General agent.

Pure function over (vault store, Ollama client, McpManager) with an
explicit pause state (docs/multi_agent_design.md D8):

    start_run(session_id, agent, query, history) -> RunResult
        build prompt via the per-agent ChatEngine, then
        LLM -> tool_calls -> MCP results -> LLM ... bounded rounds.
        A sensitive (state-mutating) tool call while
        `require_tool_confirmation` is on PAUSES the run: the full in-flight
        state is persisted to the vault (`agent_run_<session_id>`) and the
        result is `awaiting_confirmation`.
    resume_run(session_id, agent, "allow"|"deny"|"abort") -> RunResult
        reloads the persisted state and continues exactly where it paused.

Every status is a dataclass (`RunResult`); the UI is a thin projector over
it. Paused state lives in the encrypted vault - never only in
st.session_state - because Streamlit reruns the whole script on every
interaction and a rerun (or a tab close) must not lose a half-finished
trade.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.agents import store
from src.agents.mcp import McpError, McpManager, McpTool, ToolResult
from src.agents.skills import render_skills_for_prompt
from src.ai_engine.engine import build_chat_client

DENIED_RESULT_TEXT = (
    "DENIED by user. Do not retry this action; tell the user it was not "
    "performed and ask how to proceed."
)


@dataclass
class RunResult:
    """Terminal-or-pause outcome of an agent run.

    status: "complete" | "exhausted" | "awaiting_confirmation" | "aborted"
    trail: one entry per executed tool call {"server", "tool", "summary", "ok"}
    pending: the paused call {"server", "tool", "args"} (only when
    awaiting_confirmation)
    """

    status: str
    answer: str = ""
    sources: List[str] = field(default_factory=list)
    trail: List[Dict[str, Any]] = field(default_factory=list)
    pending: Optional[Dict[str, Any]] = None


@dataclass
class _ExecutedCall:
    ok: bool
    text: str
    server_name: str
    duration_ms: int
    error: Optional[str]


def assemble_system_prompt(agent: Dict[str, Any]) -> str:
    """Agent instructions + attached skill bodies, for the system prompt.

    Returns "" when the agent has neither - ChatEngine then falls back to
    its default grounding prompt (an agent with no instructions behaves
    like the grounded assistant, not like an empty one).
    """
    parts: List[str] = []
    instructions = (agent.get("instructions") or "").strip()
    if instructions:
        parts.append(instructions)
    skills = render_skills_for_prompt(agent.get("skills") or [])
    if skills:
        parts.append(skills)
    return "\n\n".join(parts)


def _args_digest(args: Any) -> str:
    try:
        digest = json.dumps(args, default=str, separators=(",", ":"))
    except Exception:
        digest = str(args)
    return digest[:200]


def _apply_notices(answer: str, notices: List[str]) -> str:
    if not notices:
        return answer
    notice = " ".join(notices)
    return f"{answer}\n\n[{notice}]" if answer else f"[{notice}]"


def _default_context_engine_factory(vault: Any) -> Callable[[Dict[str, Any]], Any]:
    """Build the per-agent ChatEngine (design D6): agent's model, context
    window, temperature, max tokens, assembled system prompt, and RAG
    category scope. The default construction (no params) is what the
    General agent's path uses, so defaults stay byte-identical."""

    def factory(agent: Dict[str, Any]):
        from src.ai_engine.chat_engine import ChatEngine

        engine = ChatEngine(
            vault_path=str(vault.vault_path),
            encryption_key=vault.encryption_key,
            model=agent.get("model") or None,
            context_window_tokens=agent.get("context_window_tokens") or None,
            system_prompt=assemble_system_prompt(agent),
            rag_scope_categories=agent.get("rag_scope_categories") or None,
        )
        temperature = agent.get("temperature")
        if isinstance(temperature, (int, float)):
            engine.ollama_client.temperature = temperature
        max_tokens = agent.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            engine.ollama_client.max_tokens = max_tokens
        return engine

    return factory


class AgentRuntime:
    def __init__(
        self,
        vault: Any,
        ollama_client: Optional[OllamaClient] = None,
        mcp: Optional[McpManager] = None,
        make_context_engine: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.vault = vault
        self.ollama_client = ollama_client or build_chat_client()
        self.mcp = mcp or McpManager(vault)
        self.make_context_engine = make_context_engine or (
            _default_context_engine_factory(vault)
        )

    # -- public API -------------------------------------------------------

    def start_run(
        self,
        session_id: str,
        agent: Dict[str, Any],
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> RunResult:
        """Run one user turn: build the grounded prompt, then the tool loop."""
        engine = self.make_context_engine(agent)
        built = engine._build_prompt(
            query,
            use_rag=bool(agent.get("rag_enabled", True)),
            history=history or [],
        )
        tools_schema, tool_by_name, tool_owner, notices = self._collect_tools(
            agent, for_answer_notice=True
        )
        chat_client = getattr(engine, "ollama_client", self.ollama_client)
        max_rounds = int(agent.get("max_tool_rounds") or 0)
        require_confirm = bool(agent.get("require_tool_confirmation"))

        if max_rounds == 0:
            response = chat_client.chat(messages=built["messages"], tools=None)
            answer = response.get("message", {}).get("content") or ""
            return RunResult(
                "complete",
                answer=_apply_notices(answer, notices),
                sources=built["sources"],
            )
        return self._run_loop(
            session_id,
            agent,
            {
                "messages": built["messages"],
                "sources": built["sources"],
                "trail": [],
                "query": query,
                "pending": None,
                "decision": None,
                "rounds_used": 0,
            },
            chat_client,
            tools_schema,
            tool_by_name,
            tool_owner,
            max_rounds,
            require_confirm,
            notices,
        )

    def resume_run(
        self, session_id: str, agent: Dict[str, Any], decision: str
    ) -> RunResult:
        """Resolve a paused run: "allow" executes the pending call, "deny"
        feeds a DENIED tool result back to the model, "abort" drops the run.
        The loop then continues from the persisted messages."""
        state = store.load_run_state(self.vault, session_id)
        if state is None:
            return RunResult("aborted", answer="[No paused run found for this session]")
        if decision == "abort":
            store.delete_run_state(self.vault, session_id)
            return RunResult("aborted", answer="[Run aborted by user]")

        engine = self.make_context_engine(agent)
        tools_schema, tool_by_name, tool_owner, notices = self._collect_tools(
            agent, for_answer_notice=False
        )
        chat_client = getattr(engine, "ollama_client", self.ollama_client)
        ctx = {
            "messages": state["messages"],
            "sources": state.get("sources", []),
            "trail": list(state.get("trail", [])),
            "query": state.get("query", ""),
            "pending": state.get("pending"),
            "decision": decision,
            "rounds_used": int(state.get("rounds_used", 0)),
        }
        return self._run_loop(
            session_id,
            agent,
            ctx,
            chat_client,
            tools_schema,
            tool_by_name,
            tool_owner,
            int(agent.get("max_tool_rounds") or 0),
            bool(agent.get("require_tool_confirmation")),
            notices,
        )

    def active_run(self, session_id: str) -> Optional[Dict[str, Any]]:
        """The persisted in-flight run for this session, if any (the UI
        renders its `pending` call as the confirmation card)."""
        return store.load_run_state(self.vault, session_id)

    # -- internals ---------------------------------------------------------

    def _collect_tools(
        self,
        agent: Dict[str, Any],
        for_answer_notice: bool,
    ):
        """Tools for the agent's enabled servers -> (schema, by_name,
        owner_by_name, notices). A server that fails to list degrades out
        (the agent runs without its tools, never silently broken); a
        capability mismatch disables all tools with a one-line notice."""
        tools: List[McpTool] = []
        notices: List[str] = []
        for server_id in agent.get("mcp_server_ids") or []:
            server = store.get_mcp_server(self.vault, server_id)
            if server is None or not server.get("enabled", False):
                continue
            try:
                tools.extend(self.mcp.list_tools(server_id))
            except McpError as exc:
                notices.append(
                    f"Tools from '{server.get('name', server_id)}' are unavailable: {exc}"
                )
        if tools and not self.ollama_client.model_supports_tools(
            agent.get("model", "")
        ):
            notices.append(
                f"Model '{agent.get('model')}' does not support tools - "
                "this agent runs without tools."
            )
            tools = []
        if not for_answer_notice:
            notices = []
        # First server wins on a name collision - consistently for the
        # exposed schema, the is_write gate, and call routing.
        tool_by_name: Dict[str, McpTool] = {}
        tool_owner: Dict[str, str] = {}
        for tool in tools:
            if tool.name in tool_by_name:
                notices.append(
                    f"Tool '{tool.name}' is provided by multiple servers; "
                    "the first one wins."
                )
                continue
            tool_by_name[tool.name] = tool
            tool_owner[tool.name] = tool.server_id
        tools_schema = [
            McpManager.to_ollama_tool(tool) for tool in tool_by_name.values()
        ]
        return tools_schema, tool_by_name, tool_owner, notices

    def _run_loop(
        self,
        session_id: str,
        agent: Dict[str, Any],
        ctx: Dict[str, Any],
        chat_client: Any,
        tools_schema: List[Dict[str, Any]],
        tool_by_name: Dict[str, McpTool],
        tool_owner: Dict[str, str],
        max_rounds: int,
        require_confirm: bool,
        notices: List[str],
    ) -> RunResult:
        messages: List[Dict[str, Any]] = ctx["messages"]
        trail: List[Dict[str, Any]] = ctx["trail"]
        sources: List[str] = ctx["sources"]

        if ctx["pending"] is not None:
            self._resolve_pending(session_id, agent, ctx, trail)
            ctx["pending"] = None

        for _ in range(max(0, max_rounds - ctx["rounds_used"])):
            ctx["rounds_used"] += 1
            response = chat_client.chat(messages=messages, tools=tools_schema or None)
            message = response.get("message", {})
            messages.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                store.delete_run_state(self.vault, session_id)
                return RunResult(
                    "complete",
                    answer=_apply_notices(
                        message.get("content") or "", notices
                    ),
                    sources=sources,
                    trail=trail,
                )
            for call in tool_calls:
                name = call.get("function", {}).get("name", "")
                args = self._coerce_args(call.get("function", {}).get("arguments"))
                tool = tool_by_name.get(name)
                server_id = tool_owner.get(name)
                if tool is None or server_id is None:
                    available = ", ".join(sorted(tool_owner)) or "none"
                    messages.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": f"Unknown tool '{name}'. Available tools: {available}",
                        }
                    )
                    trail.append(
                        {
                            "server": "",
                            "tool": name,
                            "summary": "unknown tool",
                            "ok": False,
                        }
                    )
                    continue
                if require_confirm and tool.is_write:
                    store.save_run_state(
                        self.vault,
                        session_id,
                        {
                            "agent_id": agent.get("id"),
                            "agent_name": agent.get("name"),
                            "messages": messages,
                            "sources": sources,
                            "trail": trail,
                            "pending": {
                                "server": server_id,
                                "tool": name,
                                "args": args,
                            },
                            "query": ctx["query"],
                            "rounds_used": ctx["rounds_used"],
                        },
                    )
                    return RunResult(
                        "awaiting_confirmation",
                        sources=sources,
                        trail=trail,
                        pending={
                            "server": server_id,
                            "tool": name,
                            "args": args,
                        },
                    )
                executed = self._execute_call(session_id, agent, server_id, name, args)
                messages.append(
                    {"role": "tool", "name": name, "content": executed.text}
                )
                trail.append(self._trail_entry(executed, name))

        # The "answer" is the model's last non-empty content - not a tool
        # result or the user's own query echoed back.
        last_content = next(
            (
                m.get("content")
                for m in reversed(messages)
                if m.get("role") == "assistant"
                and isinstance(m.get("content"), str)
                and m.get("content").strip()
            ),
            "",
        )
        answer = last_content or (
            f"[Stopped after {ctx['rounds_used']} tool round(s) without a final answer]"
        )
        store.delete_run_state(self.vault, session_id)
        return RunResult(
            "exhausted",
            answer=_apply_notices(answer, notices),
            sources=sources,
            trail=trail,
        )

    def _resolve_pending(
        self,
        session_id: str,
        agent: Dict[str, Any],
        ctx: Dict[str, Any],
        trail: List[Dict[str, Any]],
    ) -> None:
        """Feed the resume decision for the paused call into the message
        list (the assistant tool_calls message is already there - never
        re-emit it)."""
        pending = ctx["pending"]
        name = pending["tool"]
        args = pending["args"]
        server_id = pending["server"]
        if ctx["decision"] == "deny":
            ctx["messages"].append(
                {"role": "tool", "name": name, "content": DENIED_RESULT_TEXT}
            )
            store.log_activity(
                self.vault,
                {
                    "session_id": session_id,
                    "agent_id": agent.get("id"),
                    "agent_name": agent.get("name"),
                    "server": self._server_name(server_id),
                    "tool": name,
                    "args_digest": _args_digest(args),
                    "ok": False,
                    "duration_ms": 0,
                    "error": "denied by user",
                },
            )
            trail.append(
                {"server": self._server_name(server_id), "tool": name,
                 "summary": "denied by user", "ok": False}
            )
            return
        executed = self._execute_call(session_id, agent, server_id, name, args)
        ctx["messages"].append({"role": "tool", "name": name, "content": executed.text})
        trail.append(self._trail_entry(executed, name))

    def _execute_call(
        self,
        session_id: str,
        agent: Dict[str, Any],
        server_id: str,
        name: str,
        args: Dict[str, Any],
    ) -> _ExecutedCall:
        started = time.monotonic()
        error: Optional[str] = None
        try:
            result = self.mcp.call_tool(server_id, name, args)
        except McpError as exc:
            result = ToolResult(ok=False, text=f"Tool call failed: {exc}")
            error = str(exc)
        except Exception as exc:
            result = ToolResult(
                ok=False, text=f"Tool call failed: {type(exc).__name__}: {exc}"
            )
            error = result.text
        duration_ms = int((time.monotonic() - started) * 1000)
        store.log_activity(
            self.vault,
            {
                "session_id": session_id,
                "agent_id": agent.get("id"),
                "agent_name": agent.get("name"),
                "server": self._server_name(server_id),
                "tool": name,
                "args_digest": _args_digest(args),
                "ok": result.ok,
                "duration_ms": duration_ms,
                "error": error,
            },
        )
        return _ExecutedCall(
            ok=result.ok,
            text=result.text,
            server_name=self._server_name(server_id),
            duration_ms=duration_ms,
            error=error,
        )

    def _server_name(self, server_id: str) -> str:
        server = store.get_mcp_server(self.vault, server_id)
        return server.get("name", server_id) if server else server_id

    def _trail_entry(self, executed: _ExecutedCall, tool_name: str) -> Dict[str, Any]:
        return {
            "server": executed.server_name,
            "tool": tool_name,
            "summary": (
                f"ok ({executed.duration_ms}ms)"
                if executed.ok
                else f"error ({executed.duration_ms}ms)"
            ),
            "ok": executed.ok,
        }

    @staticmethod
    def _coerce_args(args: Any) -> Dict[str, Any]:
        """Ollama returns a dict (verified); tolerate a JSON string just in
        case so a malformed model output can't crash the loop."""
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except Exception:
                return {"raw": args}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        if args is None:
            return {}
        return {"value": args}
