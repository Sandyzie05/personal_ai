"""Unit tests for src.interface.chat (ChatMessage / ChatHistory)."""

from datetime import datetime

from src.data_vault import DataVaultError
from src.interface.chat import ChatHistory, ChatMessage


class FakeVault:
    """In-memory vault double - no encryption/SQLite involved."""

    def __init__(self):
        self.store = {}

    def store_data(self, key, data, encrypt=True):
        self.store[key] = data
        return True

    def retrieve_data(self, key):
        return self.store.get(key)

    def delete_data(self, key):
        self.store.pop(key, None)
        return True


class FailingVault:
    """Vault double that always raises, simulating data encrypted under a
    stale key (see ChatHistory._load_history's degradation path)."""

    def retrieve_data(self, key):
        raise DataVaultError("wrong key")

    def store_data(self, key, data, encrypt=True):
        return True

    def delete_data(self, key):
        return True


def test_chat_message_roundtrip():
    msg = ChatMessage("user", "hello", sources=["a", "b"])
    restored = ChatMessage.from_dict(msg.to_dict())

    assert restored.role == "user"
    assert restored.content == "hello"
    assert restored.sources == ["a", "b"]
    assert restored.timestamp == msg.timestamp


def test_chat_message_defaults():
    msg = ChatMessage("assistant", "hi")

    assert msg.sources == []
    assert isinstance(msg.timestamp, datetime)


def test_chat_history_add_message_persists_immediately():
    vault = FakeVault()
    history = ChatHistory(vault)

    history.add_message("user", "hi")
    history.add_message("assistant", "hello", sources=["doc1"])

    reloaded = ChatHistory(vault)
    messages = reloaded.get_messages()
    assert [m.content for m in messages] == ["hi", "hello"]
    assert messages[1].sources == ["doc1"]


def test_chat_history_add_exchange_writes_once():
    vault = FakeVault()
    history = ChatHistory(vault)

    write_count = 0
    original_store = vault.store_data

    def counting_store(key, data, encrypt=True):
        nonlocal write_count
        write_count += 1
        return original_store(key, data, encrypt=encrypt)

    vault.store_data = counting_store

    history.add_exchange("question", "answer", sources=["s1"])

    assert write_count == 1
    messages = history.get_messages()
    assert len(messages) == 2
    assert messages[0].role == "user" and messages[0].content == "question"
    assert messages[1].role == "assistant" and messages[1].content == "answer"
    assert messages[1].sources == ["s1"]


def test_chat_history_get_recent_messages():
    vault = FakeVault()
    history = ChatHistory(vault)
    for i in range(5):
        history.add_message("user", f"msg{i}")

    assert [m.content for m in history.get_recent_messages(2)] == ["msg3", "msg4"]


def test_chat_history_get_recent_messages_fewer_than_n():
    vault = FakeVault()
    history = ChatHistory(vault)
    history.add_message("user", "only one")

    assert [m.content for m in history.get_recent_messages(10)] == ["only one"]


def test_chat_history_clear_removes_messages_and_vault_key():
    vault = FakeVault()
    history = ChatHistory(vault)
    history.add_message("user", "hi")

    history.clear()

    assert history.get_messages() == []
    assert "chat_history" not in vault.store


def test_chat_history_degrades_to_empty_on_vault_error():
    history = ChatHistory(FailingVault())

    assert history.get_messages() == []
