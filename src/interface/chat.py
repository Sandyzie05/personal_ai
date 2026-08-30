"""Chat interface module for Personal AI System."""

import streamlit as st
from datetime import datetime
from typing import List, Dict, Any, Optional

from src.data_vault import DataVaultError

USER_AVATAR = ":material/person:"
ASSISTANT_AVATAR = ":material/smart_toy:"


class ChatMessage:
    """Represents a chat message with metadata."""

    def __init__(
        self,
        role: str,
        content: str,
        sources: List[str] = None,
        timestamp: datetime = None,
    ):
        self.role = role
        self.content = content
        self.sources = sources or []
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            role=data["role"],
            content=data["content"],
            sources=data.get("sources", []),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


class ChatHistory:
    """Manages chat conversation history with encryption."""

    def __init__(self, vault, history_key: str = "chat_history"):
        self.vault = vault
        self.history_key = history_key
        self.messages: List[ChatMessage] = []
        self._load_history()

    def _load_history(self) -> None:
        """Load chat history from vault.

        A DataVaultError here (most commonly: this key was encrypted under a
        different vault key than the one currently unlocked - e.g. leftover
        data from before a password reset) must not crash the whole chat
        page. Degrade to an empty history and surface a visible warning
        instead.
        """
        try:
            data = self.vault.retrieve_data(self.history_key)
        except DataVaultError as e:
            st.warning(
                f"Could not load saved chat history ({e}). Starting a new "
                "history - this usually means it was encrypted under a "
                "different vault key than the one unlocked now.",
                icon=":material/warning:",
            )
            self.messages = []
            return

        if data and "messages" in data:
            self.messages = [ChatMessage.from_dict(msg) for msg in data["messages"]]
        else:
            self.messages = []

    def _save_history(self) -> None:
        """Save chat history to vault."""
        data = {
            "messages": [msg.to_dict() for msg in self.messages],
            "updated_at": datetime.now().isoformat(),
        }
        self.vault.store_data(self.history_key, data)

    def add_message(self, role: str, content: str, sources: List[str] = None) -> None:
        """Add a single message to history and persist immediately."""
        self.messages.append(ChatMessage(role, content, sources))
        self._save_history()

    def add_exchange(
        self, user_content: str, assistant_content: str, sources: List[str] = None
    ) -> None:
        """Append a user message and the assistant's reply in one vault write.

        Avoids re-encrypting and re-writing the whole history twice per turn
        (once per add_message call), which doubles vault I/O for no benefit
        since both messages always land in the same turn.
        """
        self.messages.append(ChatMessage("user", user_content))
        self.messages.append(ChatMessage("assistant", assistant_content, sources))
        self._save_history()

    def get_messages(self) -> List[ChatMessage]:
        """Get all messages."""
        return self.messages.copy()

    def clear(self) -> None:
        """Clear all chat history."""
        self.messages = []
        self.vault.delete_data(self.history_key)

    def get_recent_messages(self, n: int = 10) -> List[ChatMessage]:
        """Get the most recent messages."""
        return self.messages[-n:] if self.messages else []


def render_message(message: ChatMessage) -> None:
    """Render a single message as a native Streamlit chat bubble."""
    avatar = USER_AVATAR if message.role == "user" else ASSISTANT_AVATAR
    with st.chat_message(message.role, avatar=avatar):
        st.markdown(message.content)
        st.caption(message.timestamp.strftime("%b %d, %I:%M %p"))
        render_sources(message.sources)


def render_sources(sources: Optional[List[str]]) -> None:
    """Render source citations under a message, if any."""
    if not sources:
        return
    with st.expander(
        f":material/attach_file: {len(sources)} source(s) used", expanded=False
    ):
        for i, source in enumerate(sources, 1):
            preview = source if len(source) <= 500 else source[:500] + "…"
            st.caption(f"**{i}.** {preview}")


def render_chat_history(history: ChatHistory, max_display: int = 50) -> None:
    """Render chat history oldest-to-newest, as chat bubbles.

    Only the most recent `max_display` messages are rendered per rerun -
    encrypted history can grow unbounded, and re-rendering all of it on
    every keystroke-triggered rerun would get slower the longer a vault has
    been used.
    """
    messages = history.get_messages()
    if len(messages) > max_display:
        st.caption(f"Showing the last {max_display} of {len(messages)} messages.")
    for message in messages[-max_display:]:
        render_message(message)
