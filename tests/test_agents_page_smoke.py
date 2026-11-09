"""AppTest smoke test for the Agents page and the chat agent wiring.

Pattern per AGENTS.md ("Testing the UI without a browser"): a throwaway
HOME so `AuthManager` and the vault never touch the real
`~/.personal_ai_*` files; the vault is seeded with one agent and one
DISABLED MCP server (disabled so the page makes zero network calls), the
app is unlocked with the registered DEK, and the sidebar drives
navigation. Every run must raise no exception.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.agents import store
from src.data_vault import DataVault
from src.security.auth import AuthManager

MAIN = str(Path(__file__).parent.parent / "src" / "interface" / "main.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    auth = AuthManager()
    dek = auth.register("smoke-test-password")

    seed = DataVault(vault_path=str(home / ".personal_ai_vault"), encryption_key=dek)
    server_id = store.save_mcp_server(
        seed,
        {
            "name": "Robinhood",
            "kind": "remote",
            "url": "https://agent.robinhood.com/mcp/trading",
            "enabled": False,
            "last_status": "needs_auth",
            "last_error": None,
            "tool_count": 0,
        },
    )
    agent = store.new_agent_defaults()
    agent.update(
        {
            "name": "Trader",
            "model": "qwen3.8:27b-mlx",
            "mcp_server_ids": [server_id],
            "max_tool_rounds": 6,
        }
    )
    store.create_agent(seed, agent)
    del seed

    at = AppTest.from_file(MAIN, default_timeout=120)
    at.session_state["vault_key"] = dek
    return at, home, dek


def test_chat_page_renders_with_agent_selector(app):
    app, _, _ = app
    app.run()

    assert not app.exception
    boxes = {box.label: box for box in app.selectbox}
    assert "Agent" in boxes, "the session-controls row must carry the agent selector"
    agent_box = boxes["Agent"]
    assert agent_box.value == "general", "General is the default agent"
    assert list(agent_box.options) == ["General", "Coordinator", "Expense & Records", "Forecaster", "Market", "Trader"], "General + built-ins (coordinator + specialists) + seeded Trader"


def test_stale_agent_selection_resets_to_general(app):
    """A selection pointing at a deleted agent must not crash the selectbox
    (a value missing from options raises StreamlitAPIException) - it is
    reset to General on this render."""
    app, _, _ = app
    app.session_state["active_agent_id"] = "deleted-agent-id"

    app.run()

    assert not app.exception
    assert app.session_state["active_agent_id"] == "general"


def _go_to_agents_page(app):
    app.run()
    assert not app.exception
    app.radio[0].set_value("agents").run()
    assert not app.exception


def test_agents_page_renders_seeded_agent_and_server(app):
    app, _, _ = app
    _go_to_agents_page(app)

    headers = [h.value for h in app.header]
    assert any("Agents" in h for h in headers)

    markdowns = [m.value for m in app.markdown]
    assert any("General" in m for m in markdowns), "built-in agent card"
    assert any("Trader" in m for m in markdowns), "custom agent card"

    captions = [str(c.value) for c in app.caption]
    # the Trader card's status line: model, 1 attached (but disabled) server
    assert any("1 tool server(s)" in c and "RAG on" in c for c in captions)
    assert any("Needs authorization" in c for c in captions), "MCP server status"

    buttons = [b.label for b in app.button]
    assert "New Agent" in buttons
    assert "Add server" in buttons
    assert "New skill" in buttons


def test_new_agent_form_opens_with_single_save(app):
    app, _, _ = app
    _go_to_agents_page(app)

    new_button = next(b for b in app.button if b.label == "New Agent")
    new_button.click().run()

    assert not app.exception
    labels = [e.label for e in app.expander]
    assert any("New Agent" in label for label in labels)
    input_labels = [i.label for i in app.text_input]
    assert "Name" in input_labels
    assert "Icon (Material Symbol)" in input_labels
    save_labels = [b.label for b in app.button]
    assert "Create agent" in save_labels, "one Save action for the form"
    assert "Save changes" not in save_labels


def test_paused_run_renders_confirmation_card_and_disables_input(app):
    """A vault-persisted paused run (agent_run_<session>) for the active
    agent must render the Allow/Deny/Abort card and disable the chat input
    until resolved - no model or MCP call happens to render it."""
    app, home, dek = app
    app.run()
    assert not app.exception
    session_id = app.session_state["active_chat_session_id"]

    seed = DataVault(vault_path=str(home / ".personal_ai_vault"), encryption_key=dek)
    agent = store.list_agents(seed)[0]
    store.save_run_state(
        seed,
        session_id,
        {
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "messages": [{"role": "user", "content": "buy me AAPL"}],
            "sources": [],
            "trail": [],
            "pending": {
                "server": "srv-1",
                "tool": "place_equity_order",
                "args": {"symbol": "AAPL", "quantity": "1"},
            },
            "query": "buy me AAPL",
            "rounds_used": 1,
        },
    )
    del seed

    # The session was bound to the trader agent, so it is the active one.
    app.session_state["active_agent_id"] = agent["id"]
    app.run()
    assert not app.exception

    buttons = [b.label for b in app.button]
    assert "Allow" in buttons and "Deny" in buttons and "Abort" in buttons
    assert any("AAPL" in str(j.value) for j in app.json), "pending args rendered"
    assert app.chat_input[0].disabled is True


def test_agents_page_tabs_all_render_without_network(app):
    """All four tab bodies execute on every run (Streamlit tabs are not
    lazy) - so this also proves the disabled MCP server card renders its
    status without any transport call."""
    app, _, _ = app
    _go_to_agents_page(app)

    markdowns = [m.value for m in app.markdown]
    assert any("Robinhood" in m and "remote" in m for m in markdowns), "server card"

    infos = [str(i.value) for i in app.info]
    assert any("No tool activity yet" in i for i in infos), "activity tab empty state"

    captions = [str(c.value) for c in app.caption]
    assert any("Skills are plaintext instruction files" in c for c in captions)
    assert any("Jobs run when the app is opened" in c for c in captions), "scheduled tab"
    assert not app.exception
