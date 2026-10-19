"""Offline regression for releasing unlocked session state."""

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest


def _lock_harness():
    from src.interface.main import get_app

    get_app().run()


@pytest.mark.parametrize("close_fails", [False, True])
def test_lock_releases_cached_keys_vaults_and_pending_uploads(close_fails):
    with patch("dotenv.load_dotenv"):
        interface = importlib.import_module("src.interface.main")
    key = b"synthetic-key"
    databases = [MagicMock(), MagicMock()]
    if close_fails:
        databases[0].close.side_effect = RuntimeError("synthetic close failure")
    vaults = [
        SimpleNamespace(_db=db, encryption_key=key, list_keys=lambda: [])
        for db in databases
    ]
    app = interface.PersonalAIInterface()
    app.vault = vaults[0]
    app.encryption_key = key
    app.chat_engine = SimpleNamespace(vault=vaults[1], _encryption_key=key)
    app.chat_history = SimpleNamespace(vault=vaults[0], messages=["synthetic history"])
    app._chat_history_session_id = "synthetic-session"
    app.ollama_config = {"llm_model": "synthetic-model"}
    app._initialized = True
    at = AppTest.from_file(__file__)
    state = {
        "app": app, "vault_key": key,
        "agent_runtime": SimpleNamespace(vault=vaults[0]),
        "mcp_manager": SimpleNamespace(vault=vaults[0]),
        "upload_pending_files": [{"text_preview": "synthetic private preview"}],
        "active_chat_session_id": "synthetic-session",
        "synthetic_decrypted_widget": "synthetic private value",
    }
    for name, value in state.items():
        at.session_state[name] = value
    with patch.object(interface.PersonalAIInterface, "_setup_page"), \
            patch.object(interface.PersonalAIInterface, "render_chat_page"), \
            patch.object(
                interface.PersonalAIInterface, "_render_login_gate",
                side_effect=lambda: interface.st.write("Vault locked"),
            ):
        at.run()
        assert not at.exception
        next(button for button in at.button if button.label == "Lock vault").click().run()
    assert not at.exception
    assert any(text.value == "Vault locked" for text in at.markdown)
    assert not (set(state) - {"app"}).intersection(at.session_state.filtered_state)
    assert at.session_state["app"] is not app
    assert at.session_state["app"].encryption_key is None
    assert app.encryption_key is None
    assert app.vault is None
    assert app.chat_engine is None
    assert app.chat_history is None
    assert app._chat_history_session_id is None
    assert app.ollama_config == {}
    assert app._initialized is False
    for db, vault in zip(databases, vaults):
        db.close.assert_called_once()
        assert db.encryption_key is None
        assert db._encryption is None
        assert vault.encryption_key is None
        assert vault._db is None


def test_real_registration_lock_and_password_login(tmp_path):
    interface = importlib.import_module("src.interface.main")
    home = Path.home()
    assert home.is_relative_to(tmp_path)
    assert not (home / ".personal_ai_auth").exists()
    password = "synthetic-auth-flow-password"
    at = AppTest.from_file(__file__)
    with patch.object(
        interface.PersonalAIInterface, "render_chat_page",
        autospec=True, side_effect=lambda app: app._init_vault(),
    ) as chat_page:
        at.run()
        assert not at.exception
        assert at.subheader[0].value == "Create your vault password"
        assert "vault_key" not in at.session_state
        chat_page.assert_not_called()
        at.text_input[0].set_value(password)
        at.text_input[1].set_value(password)
        next(button for button in at.button if button.label == "Create vault").click().run()
        assert not at.exception
        assert not at.error
        assert (home / ".personal_ai_auth").is_file()
        registered_key = at.session_state["vault_key"]
        registered_app = at.session_state["app"]
        registered_vault = registered_app.vault
        assert isinstance(registered_key, bytes) and len(registered_key) == 32
        assert registered_app.encryption_key == registered_key
        assert registered_vault.encryption_key == registered_key
        at.session_state["auth_flow_stale"] = "synthetic private session state"
        next(button for button in at.button if button.label == "Lock vault").click().run()
        assert not at.exception
        assert at.subheader[0].value == "Enter your vault password"
        assert "vault_key" not in at.session_state
        assert "auth_flow_stale" not in at.session_state
        assert registered_app.encryption_key is None
        assert registered_app.vault is None
        assert registered_vault.encryption_key is None
        assert registered_vault._db is None
        locked_app = at.session_state["app"]
        assert locked_app is not registered_app
        assert locked_app.encryption_key is None
        chat_page.reset_mock()
        at.text_input[0].set_value("synthetic-wrong-password")
        next(button for button in at.button if button.label == "Unlock").click().run()
        assert not at.exception
        assert any("Authentication failed" in error.value for error in at.error)
        assert at.subheader[0].value == "Enter your vault password"
        assert "vault_key" not in at.session_state
        assert locked_app.encryption_key is None
        assert locked_app.vault is None
        chat_page.assert_not_called()
        at.text_input[0].set_value(password)
        next(button for button in at.button if button.label == "Unlock").click().run()
        assert not at.exception
        assert not at.error
        assert at.session_state["vault_key"] == registered_key
        assert locked_app.encryption_key == registered_key
        assert locked_app.vault is not registered_vault
        assert locked_app.vault.encryption_key == registered_key
        assert "auth_flow_stale" not in at.session_state
        chat_page.assert_called_once_with(locked_app)
        next(button for button in at.button if button.label == "Lock vault").click().run()
        assert not at.exception
        assert "vault_key" not in at.session_state


if __name__ == "__main__":
    _lock_harness()
