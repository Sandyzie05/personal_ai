"""Unit tests for src.data_vault helpers."""

from src.data_vault import is_internal_vault_key


def test_internal_keys_detected():
    assert is_internal_vault_key("vault_metadata")
    assert is_internal_vault_key("chat_history")
    assert is_internal_vault_key("ollama_config")


def test_uploaded_document_keys_not_internal():
    assert not is_internal_vault_key("electricity_bill_20260101_abc123")
    assert not is_internal_vault_key("robinhood_statement_may")
