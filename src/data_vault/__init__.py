"""
Data Vault Module for Personal AI System

All data stored in encrypted vault using AES-256-GCM
"""

from .vault import DataVault, DataVaultError, create_vault
from .database import EncryptedSQLiteDB, DatabaseError, create_database

__all__ = ["DataVault", "DataVaultError", "create_vault", "EncryptedSQLiteDB", "DatabaseError", "create_database"]
