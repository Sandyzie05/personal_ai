"""
Data Vault Module for Personal AI System

All data stored in encrypted vault using AES-256-GCM
"""

from .vault import DataVault, DataVaultError, create_vault, is_internal_vault_key
from .database import EncryptedSQLiteDB, DatabaseError, create_database

__all__ = [
    "DataVault",
    "DataVaultError",
    "create_vault",
    "is_internal_vault_key",
    "EncryptedSQLiteDB",
    "DatabaseError",
    "create_database",
]
