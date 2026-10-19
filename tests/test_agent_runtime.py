"""Unit tests for src.agents.runtime (AgentRuntime tool loop +
confirmation gate).

All offline: a scripted FakeOllama (records the `tools=` of every call),
a FakeMcp (name -> ToolResult), a fake context engine with only
`_build_prompt`, and a real temp DataVault holding one agent and one
enabled MCP server. No Ollama, no MCP server, no network.
"""

import hashlib
import os

import pytest

from src.agents import skills as skills_module
from src.agents import store
from src.agents.mcp import McpError, McpTool
from src.agents.runtime import (
    DENIED_RESULT_TEXT,
    AgentRuntime,
    RunResult,
    _args_digest,
    assemble_system_prompt,
)
from src.data_vault import DataVault


# -- fakes ------------------------------------------------------------------


class FakeOllama:
    """Scripted chat responses; records (messages, tools) of every call."""

    def __init__(self, responses=None, tools_models=("good-model",)):
        self.responses = list(responses or [])
        self._tools_models = set(tools_models)
        self.chat_calls = []

    def chat(self, messages, stream=False, format=None, tools=None):
        self.chat_calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("FakeOllama ran out of scripted responses")
        return self.responses.pop(0)

    def model_supports_tools(self, model_name):
        return model_name in self._tools_models


class FakeMcp:
    """Fake McpManager: per-server tool lists, per-name call results.

    A result value that is an Exception instance is raised on call.
    `list_error` makes every list_tools call raise it.
    """

    def __init__(self, tools=None, results=None, list_error=None):
        self._tools = {sid: list(ts) for sid, ts in (tools or {}).items()}
        self._results = dict(results or {})
        self._list_error = list_error
        self.calls = []

    def list_tools(self, server_id):
        if self._list_error is not None:
            raise self._list_error
        if server_id not in self._tools:
            raise McpError(f"Unknown MCP server: {server_id}")
        return list(self._tools[server_id])

    def call_tool(self, server_id, name, args):
        self.calls.append((server_id, name, args))
        result = self._results.get(name, ToolResultDefault(name))
        if isinstance(result, Exception):
            raise result
        return result


class ToolResultDefault:
    """Plain stand-in for ToolResult (avoids importing it into the fake)."""

    def __init__(self, name):
        self.ok = True
        self.text = f"{name} result"


class FakeEngine:
    """Context-engine double: only `_build_prompt` is exercised. It
    deliberately has no `ollama_client` attribute, so the runtime must fall
    back to the client it was constructed with."""

    def __init__(self, messages=None, sources=None):
        self._messages = messages or [
            {"role": "system", "content": "agent system prompt"},
            {"role": "user", "content": "built prompt"},
        ]
        self._sources = list(sources or [])
        self.build_calls = []

    def _build_prompt(self, query, use_rag=True, history=None):
        self.build_calls.append(
            {"query": query, "use_rag": use_rag, "history": history}
        )
        return {
            "messages": [dict(m) for m in self._messages],
            "sources": list(self._sources),
        }


# -- helpers ------------------------------------------------------------------


def tool_call_response(name, arguments, content=""):
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": "c1", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


def final_response(text):
    return {"message": {"role": "assistant", "content": text}}


@pytest.fixture
def vault(tmp_path):
    return DataVault(
        vault_path=str(tmp_path / "vault"), encryption_key=os.urandom(32)
    )


def save_agent(vault, **overrides):
    agent = store.new_agent_defaults()
    agent["name"] = "Trader"
    agent["model"] = "good-model"
    agent["max_tool_rounds"] = 3
    agent["require_tool_confirmation"] = True
    agent.update(overrides)
    agent_id = store.create_agent(vault, agent)
    return store.get_agent(vault, agent_id)


def save_server(vault, **overrides):
    server = {
        "name": "Robinhood",
        "kind": "remote",
        "url": "https://agent.robinhood.com/mcp/trading",
        "enabled": True,
    }
    server.update(overrides)
    return store.save_mcp_server(vault, server)


def make_tool(name, server_id="srv1", is_write=False):
    return McpTool(
        server_id=server_id,
        name=name,
        description=f"{name} description",
        input_schema={"type": "object"},
        is_write=is_write,
    )


def make_runtime(vault, ollama, mcp, engine=None):
    return AgentRuntime(
        vault,
        ollama_client=ollama,
        mcp=mcp,
        make_context_engine=lambda agent: (
            engine if engine is not None else FakeEngine()
        ),
    )


def read_tool(server_id="srv1"):
    return make_tool("get_equity_quotes", server_id=server_id)


def write_tool(server_id="srv1"):
    return make_tool("place_equity_order", server_id=server_id, is_write=True)


BUY_ARGS = {"symbol": "AAPL", "quantity": "1", "side": "buy"}


def test_args_digest_is_canonical_sha256():
    args = {"z": ["privacy-canary", {"b": 2, "a": 1}], "a": "synthetic"}
    expected = hashlib.sha256(
        b'{"a":"synthetic","z":["privacy-canary",{"a":1,"b":2}]}'
    ).hexdigest()
    assert _args_digest(args) == expected
    assert _args_digest(
        {"a": "synthetic", "z": ["privacy-canary", {"a": 1, "b": 2}]}
    ) == expected
    assert _args_digest({**args, "a": "changed"}) != expected
    assert "privacy-canary" not in expected


def test_args_digest_does_not_expose_unserializable_arguments():
    args = {"canary": "privacy-canary"}
    args["cycle"] = args
    digest = _args_digest(args)
    assert len(digest) == 64
    assert "privacy-canary" not in digest


# -- start_run: the tool loop ---------------------------------------------------


class TestStartRunLoop:
    def test_read_tool_loop_completes(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[
            tool_call_response("get_equity_quotes", {"symbols": ["AAPL"]}),
            final_response("AAPL is at $212.50."),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "what's AAPL?", None)

        assert result.status == "complete"
        assert result.answer == "AAPL is at $212.50."
        assert mcp.calls == [(server_id, "get_equity_quotes", {"symbols": ["AAPL"]})]
        # the tool result re-entered the conversation as a role:tool message
        second_call_messages = ollama.chat_calls[1]["messages"]
        assert {
            "role": "tool",
            "name": "get_equity_quotes",
            "content": "get_equity_quotes result",
        } in second_call_messages
        # the first call carried exactly this tool's schema
        assert ollama.chat_calls[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "get_equity_quotes",
                    "description": "get_equity_quotes description",
                    "parameters": {"type": "object"},
                },
            }
        ]
        assert len(result.trail) == 1
        assert result.trail[0]["tool"] == "get_equity_quotes"
        assert result.trail[0]["server"] == "Robinhood"
        assert result.trail[0]["ok"] is True
        # the run state was cleaned up on the terminal status
        assert store.load_run_state(vault, "s1") is None
        activity = store.list_activity(vault)
        assert len(activity) == 1
        assert activity[0]["tool"] == "get_equity_quotes"
        assert activity[0]["ok"] is True
        assert activity[0]["server"] == "Robinhood"
        assert activity[0]["args_digest"] == _args_digest({"symbols": ["AAPL"]})
        assert "AAPL" not in result.trail[0]["summary"]

    def test_build_prompt_gets_agent_rag_settings_and_history(self, vault):
        agent = save_agent(vault, rag_enabled=False)
        engine = FakeEngine()
        runtime = make_runtime(
            vault, FakeOllama(responses=[final_response("hi")]), FakeMcp(), engine
        )
        history = [{"role": "user", "content": "earlier"}]

        runtime.start_run("s1", agent, "q", history)

        assert engine.build_calls == [
            {"query": "q", "use_rag": False, "history": history}
        ]

    def test_no_servers_passes_tools_none(self, vault):
        agent = save_agent(vault, mcp_server_ids=[])
        ollama = FakeOllama(responses=[final_response("hi")])
        runtime = make_runtime(vault, ollama, FakeMcp())

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert ollama.chat_calls[0]["tools"] is None

    def test_max_rounds_zero_is_a_single_non_tool_call(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id], max_tool_rounds=0)
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[final_response("plain answer")])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert result.answer == "plain answer"
        assert len(ollama.chat_calls) == 1
        assert ollama.chat_calls[0]["tools"] is None
        assert mcp.calls == []

    def test_rounds_cap_exhausts_without_final_answer(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id], max_tool_rounds=2)
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[
            tool_call_response("get_equity_quotes", {"symbols": ["AAPL"]}),
            tool_call_response("get_equity_quotes", {"symbols": ["NVDA"]}),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "exhausted"
        assert "2 tool round(s)" in result.answer
        assert len(ollama.chat_calls) == 2
        assert len(mcp.calls) == 2
        assert len(result.trail) == 2
        assert store.load_run_state(vault, "s1") is None

    def test_unknown_tool_is_reported_back_to_model(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[
            tool_call_response("bogus_tool", {}),
            final_response("sorry, no such tool"),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        tool_messages = [
            m for m in ollama.chat_calls[1]["messages"] if m["role"] == "tool"
        ]
        assert len(tool_messages) == 1
        assert "Unknown tool 'bogus_tool'" in tool_messages[0]["content"]
        assert "get_equity_quotes" in tool_messages[0]["content"]
        assert mcp.calls == []
        assert result.trail[0]["ok"] is False
        assert result.trail[0]["tool"] == "bogus_tool"

    def test_model_without_tools_capability_disables_tools_with_notice(
        self, vault
    ):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id], model="legacy-model")
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[final_response("hi")])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert ollama.chat_calls[0]["tools"] is None
        assert "does not support tools" in result.answer
        assert mcp.calls == []

    def test_failing_server_degrades_with_notice(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(list_error=McpError("connection refused"))
        ollama = FakeOllama(responses=[final_response("hi")])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert result.answer.startswith("hi")
        assert "Tools from 'Robinhood' are unavailable" in result.answer
        assert ollama.chat_calls[0]["tools"] is None

    def test_disabled_server_is_skipped_silently(self, vault):
        server_id = save_server(vault, enabled=False)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[final_response("ok")])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert result.answer == "ok"
        assert ollama.chat_calls[0]["tools"] is None
        assert mcp.calls == []

    def test_tool_name_collision_first_server_wins(self, vault):
        server_a = save_server(vault, name="A")
        server_b = save_server(vault, name="B")
        agent = save_agent(vault, mcp_server_ids=[server_a, server_b])
        mcp = FakeMcp(tools={
            server_a: [make_tool("search", server_id=server_a)],
            server_b: [make_tool("search", server_id=server_b)],
        })
        ollama = FakeOllama(responses=[
            tool_call_response("search", {"q": "x"}),
            final_response("done"),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.status == "complete"
        assert mcp.calls == [(server_a, "search", {"q": "x"})]
        # one schema per name, from the first server only
        assert [s["function"]["name"] for s in ollama.chat_calls[0]["tools"]] == [
            "search"
        ]
        assert "multiple servers" in result.answer

    def test_sources_from_prompt_are_passed_through(self, vault):
        agent = save_agent(vault)
        engine = FakeEngine(sources=["card-statement-1.pdf"])
        runtime = make_runtime(
            vault, FakeOllama(responses=[final_response("hi")]), FakeMcp(), engine
        )

        result = runtime.start_run("s1", agent, "q", None)

        assert result.sources == ["card-statement-1.pdf"]

    def test_engine_client_is_preferred_over_injected_one(self, vault):
        """Production builds a per-agent ChatEngine with its own Ollama
        client; the runtime must chat through it, not the injected one."""
        agent = save_agent(vault)
        injected = FakeOllama()
        engine_client = FakeOllama(responses=[final_response("from engine client")])
        engine = FakeEngine()
        engine.ollama_client = engine_client
        runtime = make_runtime(vault, injected, FakeMcp(), engine)

        result = runtime.start_run("s1", agent, "q", None)

        assert result.answer == "from engine client"
        assert injected.chat_calls == []
        assert len(engine_client.chat_calls) == 1


# -- the confirmation gate ------------------------------------------------------


class TestConfirmationGate:
    def test_sensitive_tool_pauses_and_persists_state(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[tool_call_response("place_equity_order", BUY_ARGS)])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "buy me AAPL", None)

        assert result.status == "awaiting_confirmation"
        assert result.pending == {
            "server": server_id,
            "tool": "place_equity_order",
            "args": BUY_ARGS,
        }
        # nothing was executed, nothing further asked of the model
        assert mcp.calls == []
        assert len(ollama.chat_calls) == 1
        state = store.load_run_state(vault, "s1")
        assert state is not None
        assert state["agent_id"] == agent["id"]
        assert state["agent_name"] == "Trader"
        assert state["rounds_used"] == 1
        assert state["pending"] == result.pending
        assert any(
            m.get("tool_calls") for m in state["messages"]
        ), "the assistant tool_calls message must already be in the saved state"

    def test_sensitive_tool_without_confirmation_executes(self, vault):
        server_id = save_server(vault)
        agent = save_agent(
            vault, mcp_server_ids=[server_id], require_tool_confirmation=False
        )
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[
            tool_call_response("place_equity_order", BUY_ARGS),
            final_response("bought."),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "buy me AAPL", None)

        assert result.status == "complete"
        assert result.answer == "bought."
        assert mcp.calls == [(server_id, "place_equity_order", BUY_ARGS)]
        assert store.load_run_state(vault, "s1") is None

    def test_read_tool_never_pauses_even_with_confirmation_on(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [read_tool(server_id)]})
        ollama = FakeOllama(responses=[
            tool_call_response("get_equity_quotes", {"symbols": ["AAPL"]}),
            final_response("$212.50"),
        ])
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "price?", None)

        assert result.status == "complete"
        assert store.load_run_state(vault, "s1") is None

    def test_allow_resume_executes_and_completes(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[tool_call_response("place_equity_order", BUY_ARGS)])
        runtime = make_runtime(vault, ollama, mcp)

        paused = runtime.start_run("s1", agent, "buy me AAPL", None)
        assert paused.status == "awaiting_confirmation"

        ollama.responses.append(final_response("Order placed for 1 AAPL."))
        result = runtime.resume_run("s1", agent, "allow")

        assert result.status == "complete"
        assert result.answer == "Order placed for 1 AAPL."
        assert mcp.calls == [(server_id, "place_equity_order", BUY_ARGS)]
        assert store.load_run_state(vault, "s1") is None
        assert result.trail[-1]["tool"] == "place_equity_order"
        assert result.trail[-1]["ok"] is True
        activity = store.list_activity(vault)
        assert activity[0]["tool"] == "place_equity_order"
        assert activity[0]["ok"] is True

    def test_deny_resume_feeds_denied_text_and_completes(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[tool_call_response("place_equity_order", BUY_ARGS)])
        runtime = make_runtime(vault, ollama, mcp)

        paused = runtime.start_run("s1", agent, "buy me AAPL", None)
        assert paused.status == "awaiting_confirmation"

        ollama.responses.append(final_response("Understood - not bought."))
        result = runtime.resume_run("s1", agent, "deny")

        assert result.status == "complete"
        assert result.answer == "Understood - not bought."
        assert mcp.calls == [], "a denied call must never reach the server"
        denied = [
            m for m in ollama.chat_calls[1]["messages"] if m["role"] == "tool"
        ]
        assert len(denied) == 1
        assert denied[0]["name"] == "place_equity_order"
        assert denied[0]["content"] == DENIED_RESULT_TEXT
        assert store.load_run_state(vault, "s1") is None
        activity = store.list_activity(vault)
        assert activity[0]["ok"] is False
        assert activity[0]["error"] == "denied by user"

    def test_abort_deletes_state(self, vault):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[tool_call_response("place_equity_order", BUY_ARGS)])
        runtime = make_runtime(vault, ollama, mcp)

        paused = runtime.start_run("s1", agent, "buy me AAPL", None)
        assert paused.status == "awaiting_confirmation"
        calls_before = len(ollama.chat_calls)

        result = runtime.resume_run("s1", agent, "abort")

        assert result.status == "aborted"
        assert result.answer == "[Run aborted by user]"
        assert store.load_run_state(vault, "s1") is None
        assert mcp.calls == []
        assert len(ollama.chat_calls) == calls_before

    def test_resume_without_paused_run_reports_aborted(self, vault):
        agent = save_agent(vault)
        runtime = make_runtime(vault, FakeOllama(), FakeMcp())

        result = runtime.resume_run("never-was", agent, "allow")

        assert result.status == "aborted"
        assert "No paused run found" in result.answer

    def test_resume_uses_only_remaining_rounds(self, vault):
        """A pause on the agent's last allowed round means the resume can
        only finish what it has - no extra model round is granted."""
        server_id = save_server(vault)
        agent = save_agent(
            vault, mcp_server_ids=[server_id], max_tool_rounds=1
        )
        mcp = FakeMcp(tools={server_id: [write_tool(server_id)]})
        ollama = FakeOllama(responses=[tool_call_response("place_equity_order", BUY_ARGS)])
        runtime = make_runtime(vault, ollama, mcp)

        assert (
            runtime.start_run("s1", agent, "buy me AAPL", None).status
            == "awaiting_confirmation"
        )

        result = runtime.resume_run("s1", agent, "allow")

        assert result.status == "exhausted"
        assert len(ollama.chat_calls) == 1, "no further model round after the cap"
        assert mcp.calls == [(server_id, "place_equity_order", BUY_ARGS)]
        assert store.load_run_state(vault, "s1") is None


# -- tool error handling ---------------------------------------------------------


class TestToolErrorHandling:
    def _agent_and_mcp(self, vault, result_value):
        server_id = save_server(vault)
        agent = save_agent(vault, mcp_server_ids=[server_id])
        mcp = FakeMcp(
            tools={server_id: [read_tool(server_id)]}, results={"get_equity_quotes": result_value}
        )
        ollama = FakeOllama(responses=[
            tool_call_response("get_equity_quotes", {"symbols": ["AAPL"]}),
            final_response("sorry, the tool failed"),
        ])
        return agent, mcp, ollama

    def test_mcp_error_is_fed_back_not_raised(self, vault):
        agent, mcp, ollama = self._agent_and_mcp(
            vault, McpError("connection refused")
        )
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "price?", None)

        assert result.status == "complete"
        tool_messages = [
            m for m in ollama.chat_calls[1]["messages"] if m["role"] == "tool"
        ]
        assert "Tool call failed" in tool_messages[0]["content"]
        assert "connection refused" in tool_messages[0]["content"]
        assert result.trail[0]["ok"] is False
        activity = store.list_activity(vault)
        assert activity[0]["ok"] is False
        assert activity[0]["error"]

    def test_generic_exception_is_fed_back(self, vault):
        agent, mcp, ollama = self._agent_and_mcp(vault, RuntimeError("boom"))
        runtime = make_runtime(vault, ollama, mcp)

        result = runtime.start_run("s1", agent, "price?", None)

        assert result.status == "complete"
        tool_messages = [
            m for m in ollama.chat_calls[1]["messages"] if m["role"] == "tool"
        ]
        assert "RuntimeError: boom" in tool_messages[0]["content"]


# -- assemble_system_prompt --------------------------------------------------------


class TestAssembleSystemPrompt:
    def test_returns_instructions_only_when_no_skills(self):
        agent = {"instructions": "Be terse.", "skills": []}

        assert assemble_system_prompt(agent) == "Be terse."

    def test_empty_when_agent_has_nothing(self):
        assert assemble_system_prompt({"instructions": "", "skills": []}) == ""
        assert assemble_system_prompt({}) == ""

    def test_includes_skill_bodies(self, monkeypatch, tmp_path):
        monkeypatch.setattr(skills_module, "SKILLS_DIR", str(tmp_path / "skills"))
        skills_module.write_skill(
            "discipline",
            "Trading Discipline",
            "Force conservative framing",
            "State position size before any trade.",
        )
        agent = {"instructions": "Instr.", "skills": ["discipline"]}

        prompt = assemble_system_prompt(agent)

        assert "Instr." in prompt
        assert "## Skill: Trading Discipline" in prompt
        assert "State position size before any trade." in prompt

    def test_missing_skill_slugs_are_skipped(self):
        agent = {"instructions": "Instr.", "skills": ["no-such-skill"]}

        assert assemble_system_prompt(agent) == "Instr."


# -- RunResult / arg coercion -------------------------------------------------------


class TestCoerceArgs:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"a": 1}, {"a": 1}),
            ('{"a": 1}', {"a": 1}),
            ("not-json", {"raw": "not-json"}),
            (None, {}),
            ("[1, 2]", {"value": [1, 2]}),
            (42, {"value": 42}),
        ],
    )
    def test_coercion(self, raw, expected):
        assert AgentRuntime._coerce_args(raw) == expected


class TestRunResult:
    def test_defaults(self):
        result = RunResult("complete")
        assert result.answer == ""
        assert result.sources == []
        assert result.trail == []
        assert result.pending is None
