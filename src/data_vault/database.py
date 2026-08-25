"""Encrypted SQLite database module for Personal AI System."""

import sqlite3
from typing import Optional, Dict, Any, List
from pathlib import Path
import json
import datetime

from ..security.encryption import AEADEncryption


class DatabaseError(Exception):
    """Custom exception for database operations."""

    pass


class EncryptedSQLiteDB:
    """SQLite database with encrypted BLOB columns."""

    def __init__(self, db_path: str, encryption_key: Optional[bytes] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.encryption_key = encryption_key
        self._encryption: Optional[AEADEncryption] = None

        if self.encryption_key:
            self._encryption = AEADEncryption(key=self.encryption_key)

        self.conn = self._create_connection()
        self._initialize_schema()

    def _create_connection(self) -> sqlite3.Connection:
        """Create database connection.

        `check_same_thread=False`: the caller (Streamlit) caches this
        connection in `st.session_state` and reuses it across script
        reruns, each of which runs on its own thread. Streamlit guarantees
        reruns for a given session run one at a time, never concurrently,
        so disabling sqlite3's same-thread check is safe here.
        """
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _initialize_schema(self) -> None:
        """Initialize database schema."""
        cursor = self.conn.cursor()

        # Data entries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS data_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                encrypted_data BLOB NOT NULL,
                is_encrypted INTEGER NOT NULL DEFAULT 1,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Vault metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()

    def store_data(self, key: str, data: Dict[str, Any], encrypt: bool = True) -> bool:
        """Store data in the database."""
        try:
            cursor = self.conn.cursor()

            if encrypt and not self._encryption:
                raise DatabaseError(
                    "Refusing to store data: encrypt=True was requested but no "
                    "encryption key is loaded. Pass encrypt=False explicitly if "
                    "unencrypted storage is really intended."
                )

            if encrypt:
                encrypted_data = self._encryption.encrypt_string(json.dumps(data))
                is_encrypted = 1
            else:
                encrypted_data = json.dumps(data)
                is_encrypted = 0

            cursor.execute(
                """
                INSERT OR REPLACE INTO data_entries (key, encrypted_data, is_encrypted, metadata)
                VALUES (?, ?, ?, ?)
            """,
                (
                    key,
                    encrypted_data,
                    is_encrypted,
                    json.dumps({"updated_at": datetime.datetime.now().isoformat()}),
                ),
            )

            self.conn.commit()
            return True

        except Exception as e:
            raise DatabaseError(f"Failed to store data: {str(e)}")

    def retrieve_data(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve data from the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT encrypted_data, is_encrypted FROM data_entries WHERE key = ?",
                (key,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            encrypted_data = row["encrypted_data"]
            is_encrypted = row["is_encrypted"]

            if is_encrypted and self._encryption:
                decrypted = self._encryption.decrypt_string(encrypted_data)
                return json.loads(decrypted)
            else:
                return json.loads(encrypted_data)

        except Exception as e:
            raise DatabaseError(f"Failed to retrieve data: {str(e)}")

    def list_keys(self) -> List[str]:
        """List all keys in the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT key FROM data_entries")
            return [row["key"] for row in cursor.fetchall()]
        except Exception as e:
            raise DatabaseError(f"Failed to list keys: {str(e)}")

    def delete_data(self, key: str) -> bool:
        """Delete data from the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM data_entries WHERE key = ?", (key,))
            self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            raise DatabaseError(f"Failed to delete data: {str(e)}")

    def clear(self) -> bool:
        """Clear all data from the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM data_entries")
            self.conn.commit()
            return True
        except Exception as e:
            raise DatabaseError(f"Failed to clear database: {str(e)}")

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()


def create_database(
    db_path: str, encryption_key: Optional[bytes] = None
) -> EncryptedSQLiteDB:
    """Factory function to create a database instance."""
    return EncryptedSQLiteDB(db_path=db_path, encryption_key=encryption_key)
