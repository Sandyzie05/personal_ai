"""Unit tests for src.data_vault helpers."""

from src.data_vault import is_internal_vault_key


def test_internal_keys_detected():
    assert is_internal_vault_key("vault_metadata")
    assert is_internal_vault_key("chat_history")
    assert is_internal_vault_key("ollama_config")


def test_uploaded_document_keys_not_internal():
    assert not is_internal_vault_key("electricity_bill_20260101_abc123")
    assert not is_internal_vault_key("robinhood_statement_may")


def test_chat_history_prefix_is_internal():
    assert is_internal_vault_key("chat_history_abc123")
    assert is_internal_vault_key("chat_history_legacy")


def test_chat_sessions_index_is_internal():
    assert is_internal_vault_key("chat_sessions_index")


def test_dashboard_config_is_internal():
    assert is_internal_vault_key("dashboard_config")


def test_unrelated_key_with_similar_prefix_not_internal():
    assert not is_internal_vault_key("electric_bill_20260101")
