"""Unit tests for src.agents.store (vault-backed agent/MCP/activity/run
state CRUD), against a real temp DataVault - no Streamlit involved."""

import os

import pytest

from src.agents import store
from src.data_vault import DataVault


@pytest.fixture
def vault(tmp_path):
    return DataVault(
        vault_path=str(tmp_path / "vault"), encryption_key=os.urandom(32)
    )


def make_agent(**overrides):
    agent = store.new_agent_defaults()
    agent["name"] = "Trader"
    agent.update(overrides)
    return agent


class TestGeneralAgent:
    def test_get_agent_returns_general_for_builtin_id(self, vault):
        agent = store.get_agent(vault, store.GENERAL_AGENT_ID)

        assert agent is not None
        assert agent["id"] == "general"
        assert agent["built_in"] is True
        assert agent["rag_enabled"] is True
        assert agent["max_tool_rounds"] == 0

    def test_general_agent_is_not_in_custom_list(self, vault):
        store.create_agent(vault, make_agent())

        ids = [a["id"] for a in store.list_agents(vault)]

        assert store.GENERAL_AGENT_ID not in ids
        assert len(ids) == 1


class TestAgentCrud:
    def test_create_assigns_id_and_timestamps(self, vault):
        agent = make_agent()
        agent.pop("id", None)

        agent_id = store.create_agent(vault, agent)

        stored = store.get_agent(vault, agent_id)
        assert stored["id"] == agent_id
        assert stored["built_in"] is False
        assert stored["created_at"]
        assert stored["updated_at"] == stored["created_at"]
        assert stored["name"] == "Trader"

    def test_list_returns_creation_order(self, vault):
        first = store.create_agent(vault, make_agent(name="A"))
        second = store.create_agent(vault, make_agent(name="B"))

        listed = store.list_agents(vault)

        assert [a["id"] for a in listed] == [first, second]

    def test_update_replaces_fields_preserving_id_and_created_at(self, vault):
        agent_id = store.create_agent(vault, make_agent(name="Old"))
        created_at = store.get_agent(vault, agent_id)["created_at"]

        updated = store.get_agent(vault, agent_id)
        updated["name"] = "New"
        updated["model"] = "qwen3.8:27b-mlx"
        assert store.update_agent(vault, agent_id, updated) is True

        stored = store.get_agent(vault, agent_id)
        assert stored["name"] == "New"
        assert stored["model"] == "qwen3.8:27b-mlx"
        assert stored["id"] == agent_id
        assert stored["created_at"] == created_at
        assert stored["updated_at"] >= created_at

    def test_update_cannot_touch_builtin_or_unknown(self, vault):
        assert store.update_agent(vault, store.GENERAL_AGENT_ID, make_agent()) is False
        assert store.update_agent(vault, "nope", make_agent()) is False

    def test_delete_removes_and_is_idempotent(self, vault):
        agent_id = store.create_agent(vault, make_agent())

        assert store.delete_agent(vault, agent_id) is True
        assert store.get_agent(vault, agent_id) is None
        assert store.delete_agent(vault, agent_id) is False

    def test_delete_cannot_touch_builtin(self, vault):
        assert store.delete_agent(vault, store.GENERAL_AGENT_ID) is False
        assert store.get_agent(vault, store.GENERAL_AGENT_ID) is not None

    def test_create_cannot_become_builtin(self, vault):
        agent = make_agent()
        agent["built_in"] = True

        agent_id = store.create_agent(vault, agent)

        assert store.get_agent(vault, agent_id)["built_in"] is False


class TestMcpServers:
    def test_save_inserts_then_upserts_by_id(self, vault):
        server = {"name": "Robinhood", "kind": "remote", "enabled": True}

        server_id = store.save_mcp_server(vault, dict(server))
        assert server_id
        created_at = store.get_mcp_server(vault, server_id)["created_at"]
        store.save_mcp_server(
            vault, dict(server, id=server_id, url="https://example.com/mcp")
        )

        servers = store.list_mcp_servers(vault)
        assert len(servers) == 1
        stored = store.get_mcp_server(vault, server_id)
        assert stored is not None
        assert stored["url"] == "https://example.com/mcp"
        assert stored["created_at"] == created_at

    def test_save_without_id_creates_new(self, vault):
        first = store.save_mcp_server(vault, {"name": "One", "kind": "stdio"})
        second = store.save_mcp_server(vault, {"name": "One", "kind": "stdio"})

        assert first != second
        assert len(store.list_mcp_servers(vault)) == 2

    def test_update_status_patches_volatile_fields_only(self, vault):
        server_id = store.save_mcp_server(
            vault, {"name": "R", "kind": "remote", "enabled": True}
        )

        assert store.update_mcp_server_status(
            vault, server_id, last_status="connected", tool_count=41, last_error=None
        ) is True

        stored = store.get_mcp_server(vault, server_id)
        assert stored["last_status"] == "connected"
        assert stored["tool_count"] == 41
        assert stored["name"] == "R"

    def test_update_status_unknown_server_is_noop(self, vault):
        assert store.update_mcp_server_status(vault, "nope", last_status="error") is False

    def test_delete(self, vault):
        server_id = store.save_mcp_server(vault, {"name": "R", "kind": "remote"})

        assert store.delete_mcp_server(vault, server_id) is True
        assert store.get_mcp_server(vault, server_id) is None
        assert store.delete_mcp_server(vault, server_id) is False


class TestActivityLog:
    def test_log_and_list_newest_first(self, vault):
        store.log_activity(vault, {"agent_name": "A", "tool": "t1"})
        store.log_activity(vault, {"agent_name": "B", "tool": "t2"})

        entries = store.list_activity(vault)

        assert [e["tool"] for e in entries] == ["t2", "t1"]
        assert all("ts" in e for e in entries)

    def test_entries_are_capped_dropping_oldest(self, vault):
        for i in range(store.AGENT_ACTIVITY_MAX_ENTRIES + 50):
            store.log_activity(vault, {"tool": f"t{i}"})

        entries = store.list_activity(vault, limit=10_000)

        assert len(entries) == store.AGENT_ACTIVITY_MAX_ENTRIES
        assert entries[0]["tool"] == f"t{store.AGENT_ACTIVITY_MAX_ENTRIES + 49}"

    def test_list_respects_limit(self, vault):
        for i in range(10):
            store.log_activity(vault, {"tool": f"t{i}"})

        assert len(store.list_activity(vault, limit=3)) == 3

    def test_log_never_raises(self, vault):
        class BrokenVault:
            def store_data(self, *args, **kwargs):
                raise RuntimeError("vault on fire")

            def retrieve_data(self, *args, **kwargs):
                raise RuntimeError("vault on fire")

        store.log_activity(BrokenVault(), {"tool": "t"})


class TestRunState:
    def test_save_load_delete_roundtrip(self, vault):
        state = {"agent_id": "a1", "messages": [{"role": "user", "content": "q"}]}

        store.save_run_state(vault, "s1", state)
        loaded = store.load_run_state(vault, "s1")

        assert loaded["agent_id"] == "a1"
        assert loaded["messages"] == state["messages"]
        assert loaded["created_at"]

        store.delete_run_state(vault, "s1")
        assert store.load_run_state(vault, "s1") is None

    def test_run_key_namespacing(self):
        assert store.run_key_for("s1") == "agent_run_s1"

    def test_load_missing_session_returns_none(self, vault):
        assert store.load_run_state(vault, "never-was") is None

    def test_delete_missing_state_does_not_raise(self, vault):
        store.delete_run_state(vault, "never-was")
