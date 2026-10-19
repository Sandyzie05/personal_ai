"""Unit tests for src.data_vault helpers."""

import pytest

from src.data_vault import DataVault, DataVaultError, is_internal_vault_key


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


def test_forecast_records_are_internal_but_uploaded_forecast_documents_are_not():
    assert is_internal_vault_key("forecast_snapshot_abc123")
    assert is_internal_vault_key("forecast_outcome_abc123")
    assert not is_internal_vault_key("forecast_report_20261019")


def test_immutable_records_are_encrypted_idempotent_and_never_overwritten(tmp_path):
    vault = DataVault(str(tmp_path / "synthetic-vault"), encryption_key=b"x" * 32)
    payload = {"public_symbol": "SYNTHETIC", "forecast_id": "test"}
    assert vault.store_immutable_data("forecast_snapshot_test", payload)
    assert not vault.store_immutable_data("forecast_snapshot_test", dict(payload))
    with pytest.raises(DataVaultError, match="different content"):
        vault.store_immutable_data("forecast_snapshot_test", {"public_symbol": "OTHER"})
    assert vault.retrieve_data("forecast_snapshot_test") == payload
    row = vault._db.conn.execute(
        "SELECT encrypted_data, is_encrypted FROM data_entries WHERE key = ?",
        ("forecast_snapshot_test",),
    ).fetchone()
    assert row["is_encrypted"] == 1
    assert "SYNTHETIC" not in str(row["encrypted_data"])
    vault._db.conn.close()


def test_immutable_records_require_unlocked_encryption_key(tmp_path):
    vault = DataVault(str(tmp_path / "synthetic-locked-vault"))
    with pytest.raises(DataVaultError, match="encryption key"):
        vault.store_immutable_data("forecast_snapshot_test", {"test": True})
    vault._db.conn.close()
