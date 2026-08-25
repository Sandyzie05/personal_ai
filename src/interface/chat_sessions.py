"""Pure-ish chat session index management on top of the data vault.

Multiple named chat sessions are tracked in one vault record at
`SESSIONS_INDEX_KEY`. Each session's actual messages live under their own
vault key (see `history_key_for`), read/written via `ChatHistory` elsewhere -
this module only manages the index (create/list/touch/delete) and the
one-time migration of the legacy single-conversation history into that
index.

Kept free of direct Streamlit imports (aside from nothing at all here) so
the session-index logic is unit-testable against a real `DataVault` without
mocking Streamlit, matching the pattern used by `file_grouping.py`.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.data_vault import DataVaultError

SESSIONS_INDEX_KEY = "chat_sessions_index"
LEGACY_HISTORY_KEY = "chat_history"
LEGACY_SESSION_ID = "legacy"
DEFAULT_TITLE = "New Chat"


def history_key_for(session_id: str) -> str:
    """Vault key under which a session's messages are stored.

    The legacy migrated session keeps using the original fixed
    `"chat_history"` key (its messages are never copied/rewritten) rather
    than `"chat_history_legacy"` - see `migrate_legacy_history`.
    """
    if session_id == LEGACY_SESSION_ID:
        return LEGACY_HISTORY_KEY
    return f"chat_history_{session_id}"


def _load_index(vault: Any) -> Dict[str, Any]:
    """Load the sessions index, degrading to an empty index on any failure.

    Mirrors the degrade-to-empty pattern used by `ChatHistory._load_history`
    - a decrypt failure (e.g. vault key mismatch) must not crash the page.
    """
    try:
        data = vault.retrieve_data(SESSIONS_INDEX_KEY)
    except DataVaultError:
        return {"sessions": []}
    if not data or "sessions" not in data:
        return {"sessions": []}
    return data


def _save_index(vault: Any, index: Dict[str, Any]) -> None:
    vault.store_data(SESSIONS_INDEX_KEY, index)


def list_sessions(vault: Any) -> List[Dict[str, Any]]:
    """All known chat sessions, most recently active first."""
    index = _load_index(vault)
    sessions = list(index.get("sessions", []))
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


def create_session(vault: Any, title: str = DEFAULT_TITLE) -> str:
    """Create a new chat session, persist it, and return its id."""
    session_id = uuid.uuid4().hex
    now = datetime.now().isoformat()
    session = {
        "id": session_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "history_key": history_key_for(session_id),
    }
    index = _load_index(vault)
    index.setdefault("sessions", []).append(session)
    _save_index(vault, index)
    return session_id


def touch_session(vault: Any, session_id: str, title: Optional[str] = None) -> None:
    """Bump `updated_at` for `session_id`; adopt `title` if still default.

    No-op if `session_id` isn't found in the index (e.g. it was deleted
    concurrently in another tab).
    """
    index = _load_index(vault)
    sessions = index.get("sessions", [])
    for session in sessions:
        if session.get("id") == session_id:
            session["updated_at"] = datetime.now().isoformat()
            if title and session.get("title") == DEFAULT_TITLE:
                session["title"] = title
            _save_index(vault, index)
            return


def delete_session(vault: Any, session_id: str) -> None:
    """Remove a session from the index and delete its message history."""
    index = _load_index(vault)
    sessions = index.get("sessions", [])

    history_key = history_key_for(session_id)
    for session in sessions:
        if session.get("id") == session_id:
            history_key = session.get("history_key", history_key)
            break

    remaining = [s for s in sessions if s.get("id") != session_id]
    index["sessions"] = remaining
    _save_index(vault, index)

    try:
        vault.delete_data(history_key)
    except Exception:
        pass


def migrate_legacy_history(vault: Any) -> Optional[str]:
    """One-time migration of the legacy single-conversation history.

    If no sessions index exists yet and the legacy `"chat_history"` key has
    at least one message, wrap it in a session entry (id="legacy", title
    "Previous Chat") that points its `history_key` at the existing
    `"chat_history"` key - the messages themselves are never copied. Idempotent:
    once an index exists (even an empty one, or one that already contains
    this migration), this does nothing.

    Returns the new session's id, or None if no migration was performed.
    """
    try:
        existing_index = vault.retrieve_data(SESSIONS_INDEX_KEY)
    except DataVaultError:
        # Can't tell if an index exists; don't risk clobbering it.
        return None
    if existing_index is not None:
        return None

    try:
        legacy_data = vault.retrieve_data(LEGACY_HISTORY_KEY)
    except DataVaultError:
        return None
    if not legacy_data or not legacy_data.get("messages"):
        return None

    now = legacy_data.get("updated_at") or datetime.now().isoformat()
    session = {
        "id": LEGACY_SESSION_ID,
        "title": "Previous Chat",
        "created_at": now,
        "updated_at": now,
        "history_key": LEGACY_HISTORY_KEY,
    }
    _save_index(vault, {"sessions": [session]})
    return LEGACY_SESSION_ID
