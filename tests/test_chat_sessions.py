"""Unit tests for src.interface.chat_sessions (real DataVault, temp dir)."""

import os

import pytest

from src.data_vault import DataVault
from src.interface import chat_sessions


@pytest.fixture
def vault(tmp_path):
    return DataVault(vault_path=str(tmp_path / "vault"), encryption_key=os.urandom(32))


def test_create_session_appears_in_list(vault):
    session_id = chat_sessions.create_session(vault)

    sessions = chat_sessions.list_sessions(vault)

    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id
    assert sessions[0]["title"] == "New Chat"
    assert sessions[0]["history_key"] == chat_sessions.history_key_for(session_id)


def test_list_sessions_empty_when_none_created(vault):
    assert chat_sessions.list_sessions(vault) == []


def test_list_sessions_ordered_by_updated_at_desc(vault):
    first_id = chat_sessions.create_session(vault, title="First")
    second_id = chat_sessions.create_session(vault, title="Second")

    # Touch the first session so it becomes more recently updated than the second.
    chat_sessions.touch_session(vault, first_id)

    sessions = chat_sessions.list_sessions(vault)

    assert [s["id"] for s in sessions] == [first_id, second_id]


def test_touch_session_updates_updated_at(vault):
    session_id = chat_sessions.create_session(vault)
    original = chat_sessions.list_sessions(vault)[0]["updated_at"]

    chat_sessions.touch_session(vault, session_id)

    updated = chat_sessions.list_sessions(vault)[0]["updated_at"]
    assert updated >= original


def test_touch_session_sets_title_only_when_still_default(vault):
    session_id = chat_sessions.create_session(vault)

    chat_sessions.touch_session(vault, session_id, title="What's my electric bill?")
    sessions = chat_sessions.list_sessions(vault)
    assert sessions[0]["title"] == "What's my electric bill?"

    # Second call with a different message shouldn't clobber the real title.
    chat_sessions.touch_session(vault, session_id, title="A different message")
    sessions = chat_sessions.list_sessions(vault)
    assert sessions[0]["title"] == "What's my electric bill?"


def test_touch_session_noop_for_unknown_id(vault):
    chat_sessions.create_session(vault)

    # Should not raise, and should not affect existing sessions.
    chat_sessions.touch_session(vault, "does-not-exist", title="whatever")

    sessions = chat_sessions.list_sessions(vault)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "New Chat"


def test_delete_session_removes_from_index_and_deletes_history(vault):
    session_id = chat_sessions.create_session(vault)
    history_key = chat_sessions.history_key_for(session_id)
    vault.store_data(history_key, {"messages": [{"role": "user", "content": "hi"}]})

    chat_sessions.delete_session(vault, session_id)

    assert chat_sessions.list_sessions(vault) == []
    assert vault.retrieve_data(history_key) is None


def test_delete_session_ignores_missing_history_key(vault):
    session_id = chat_sessions.create_session(vault)

    # No history was ever stored for this session; should not raise.
    chat_sessions.delete_session(vault, session_id)

    assert chat_sessions.list_sessions(vault) == []


def test_migrate_legacy_history_creates_session_when_legacy_present(vault):
    vault.store_data(
        chat_sessions.LEGACY_HISTORY_KEY,
        {
            "messages": [{"role": "user", "content": "old message"}],
            "updated_at": "2026-01-01T00:00:00",
        },
    )

    new_id = chat_sessions.migrate_legacy_history(vault)

    assert new_id == chat_sessions.LEGACY_SESSION_ID
    sessions = chat_sessions.list_sessions(vault)
    assert len(sessions) == 1
    assert sessions[0]["id"] == chat_sessions.LEGACY_SESSION_ID
    assert sessions[0]["title"] == "Previous Chat"
    assert sessions[0]["history_key"] == chat_sessions.LEGACY_HISTORY_KEY
    # The legacy messages themselves must remain readable under the same key.
    legacy_data = vault.retrieve_data(chat_sessions.LEGACY_HISTORY_KEY)
    assert legacy_data["messages"] == [{"role": "user", "content": "old message"}]


def test_migrate_legacy_history_noop_when_no_legacy_history(vault):
    result = chat_sessions.migrate_legacy_history(vault)

    assert result is None
    assert chat_sessions.list_sessions(vault) == []


def test_migrate_legacy_history_is_idempotent(vault):
    vault.store_data(
        chat_sessions.LEGACY_HISTORY_KEY,
        {"messages": [{"role": "user", "content": "old message"}]},
    )

    first = chat_sessions.migrate_legacy_history(vault)
    assert first == chat_sessions.LEGACY_SESSION_ID

    second = chat_sessions.migrate_legacy_history(vault)
    assert second is None
    # Still just the one migrated session, not duplicated.
    assert len(chat_sessions.list_sessions(vault)) == 1


def test_migrate_legacy_history_noop_when_index_already_exists(vault):
    chat_sessions.create_session(vault, title="Existing")
    vault.store_data(
        chat_sessions.LEGACY_HISTORY_KEY,
        {"messages": [{"role": "user", "content": "old message"}]},
    )

    result = chat_sessions.migrate_legacy_history(vault)

    assert result is None
    sessions = chat_sessions.list_sessions(vault)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Existing"


def test_migrate_legacy_history_noop_when_legacy_has_no_messages(vault):
    vault.store_data(chat_sessions.LEGACY_HISTORY_KEY, {"messages": []})

    result = chat_sessions.migrate_legacy_history(vault)

    assert result is None
    assert chat_sessions.list_sessions(vault) == []
