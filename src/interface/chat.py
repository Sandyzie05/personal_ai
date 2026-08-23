"""Chat interface module for Personal AI System."""

import streamlit as st
from datetime import datetime
from typing import List, Dict, Any, Optional


class ChatMessage:
    """Represents a chat message with metadata."""
    
    def __init__(self, role: str, content: str, sources: List[str] = None, 
                 timestamp: datetime = None):
        self.role = role
        self.content = content
        self.sources = sources or []
        self.timestamp = timestamp or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "timestamp": self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            role=data["role"],
            content=data["content"],
            sources=data.get("sources", []),
            timestamp=datetime.fromisoformat(data["timestamp"])
        )


class ChatHistory:
    """Manages chat conversation history with encryption."""
    
    def __init__(self, vault, history_key: str = "chat_history"):
        self.vault = vault
        self.history_key = history_key
        self.messages: List[ChatMessage] = []
        self._load_history()
    
    def _load_history(self) -> None:
        """Load chat history from vault."""
        data = self.vault.retrieve_data(self.history_key)
        if data and "messages" in data:
            self.messages = [ChatMessage.from_dict(msg) for msg in data["messages"]]
        else:
            self.messages = []
    
    def _save_history(self) -> None:
        """Save chat history to vault."""
        data = {
            "messages": [msg.to_dict() for msg in self.messages],
            "updated_at": datetime.now().isoformat()
        }
        self.vault.store_data(self.history_key, data)
    
    def add_message(self, role: str, content: str, sources: List[str] = None) -> None:
        """Add a new message to history."""
        message = ChatMessage(role, content, sources)
        self.messages.append(message)
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


def display_message(message: ChatMessage, expanded: bool = True) -> None:
    """Display a single chat message in Streamlit."""
    role = "👤 You" if message.role == "user" else "🤖 Assistant"
    with st.expander(f"{role} ({message.timestamp.strftime('%Y-%m-%d %H:%M')})", expanded=expanded):
        st.write(message.content)
        
        if message.sources:
            with st.expander("📊 Sources", expanded=False):
                for i, source in enumerate(message.sources, 1):
                    st.text(f"{i}. {source}")


def display_chat_history(history: ChatHistory, max_display: int = 20) -> None:
    """Display recent chat history (limit to avoid performance issues)."""
    messages = history.get_messages()
    # Show most recent messages first, limit display
    recent_messages = list(reversed(messages[-max_display:]))
    
    if len(messages) > max_display:
        st.info(f" Showing {max_display} of {len(messages)} messages. Use Search to find older messages.")
    
    for message in recent_messages:
        display_message(message)
