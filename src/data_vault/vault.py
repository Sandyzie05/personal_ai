"""Data Vault Module for Personal AI System - SQLite backend."""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from .database import EncryptedSQLiteDB, DatabaseError


class DataVault:
    """Encrypted data vault for personal information using SQLite."""
    
    def __init__(self, vault_path: Optional[str] = None, encryption_key: Optional[bytes] = None):
        self.vault_path = Path(vault_path or os.path.expanduser("~/.personal_ai_vault"))
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.vault_path / "vault.db"
        self.encryption_key = encryption_key
        self._db: Optional[EncryptedSQLiteDB] = None
        
        # Always create database connection
        self._db = EncryptedSQLiteDB(str(self.db_path), encryption_key=self.encryption_key)
        
        self._ensure_metadata()
    
    def _ensure_metadata(self) -> None:
        """Initialize or load vault metadata."""
        try:
            metadata = {
                "version": "1.0.0",
                "created_at": self._get_timestamp(),
                "encryption_method": "AES-256-GCM" if self.encryption_key else "None",
                "storage_backend": "SQLite"
            }
            if self._db:
                self._db.store_data("vault_metadata", metadata, encrypt=False)
        except Exception as e:
            pass
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        import datetime
        return datetime.datetime.now().isoformat()
    
    def store_data(self, key: str, data: Dict[str, Any], encrypt: bool = True) -> bool:
        """Store data in the vault."""
        try:
            if self._db is None:
                raise DataVaultError("Cannot store data without encryption key")
            
            self._db.store_data(key, data, encrypt=encrypt)
            return True
        except DatabaseError as e:
            raise DataVaultError(f"Failed to store data: {str(e)}")
    
    def retrieve_data(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve data from the vault."""
        try:
            if self._db is None:
                raise DataVaultError("Cannot retrieve data without encryption key")
            
            return self._db.retrieve_data(key)
        except DatabaseError as e:
            raise DataVaultError(f"Failed to retrieve data: {str(e)}")
    
    def list_keys(self) -> List[str]:
        """List all keys in the vault."""
        try:
            if self._db is None:
                return []
            
            return self._db.list_keys()
        except DatabaseError as e:
            raise DataVaultError(f"Failed to list keys: {str(e)}")
    
    def delete_data(self, key: str) -> bool:
        """Delete data from the vault."""
        try:
            if self._db is None:
                return False
            
            return self._db.delete_data(key)
        except DatabaseError as e:
            raise DataVaultError(f"Failed to delete data: {str(e)}")
    
    def clear(self) -> bool:
        """Clear all data from the vault."""
        try:
            if self._db is None:
                return False
            
            return self._db.clear()
        except DatabaseError as e:
            raise DataVaultError(f"Failed to clear vault: {str(e)}")


class DataVaultError(Exception):
    """Custom exception for data vault operations."""
    pass


def create_vault(vault_path: Optional[str] = None, encryption_key: Optional[bytes] = None) -> DataVault:
    """Factory function to create a new DataVault instance."""
    return DataVault(vault_path=vault_path, encryption_key=encryption_key)
