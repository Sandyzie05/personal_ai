"""
Key Manager Module for Personal AI System
"""
import os
import base64
from typing import Optional
from .encryption import AEADEncryption


class KeyManager:
    """Manages encryption keys for the personal AI system."""
    
    def __init__(self, service_name: str = "personal_ai"):
        self.service_name = service_name
        self.keyring = None
        try:
            import keyring
            self.keyring = keyring
        except ImportError:
            self.keyring = None
    
    def generate_key(self, key_id: str = "default", key_size: int = 32) -> bytes:
        """Generate a new encryption key."""
        key = os.urandom(key_size)
        if self.store_key(key_id, key):
            return key
        raise RuntimeError("Failed to store generated key")
    
    def store_key(self, key_id: str, key: bytes) -> bool:
        """Store an encryption key."""
        if self.keyring:
            try:
                self.keyring.set_password(
                    self.service_name, key_id, base64.b64encode(key).decode("utf-8")
                )
                return True
            except Exception:
                pass
        
        key_path = os.path.expanduser(f"~/.personal_ai_{key_id}_key")
        with open(key_path, "wb") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
        return True
    
    def get_key(self, key_id: str = "default") -> Optional[bytes]:
        """Retrieve an encryption key."""
        if self.keyring:
            try:
                key_str = self.keyring.get_password(self.service_name, key_id)
                if key_str:
                    return base64.b64decode(key_str.encode("utf-8"))
            except Exception:
                pass
        
        key_path = os.path.expanduser(f"~/.personal_ai_{key_id}_key")
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return f.read()
        return None
    
    def delete_key(self, key_id: str) -> bool:
        """Delete an encryption key."""
        if self.keyring:
            try:
                self.keyring.delete_password(self.service_name, key_id)
                return True
            except Exception:
                pass
        
        key_path = os.path.expanduser(f"~/.personal_ai_{key_id}_key")
        if os.path.exists(key_path):
            os.remove(key_path)
            return True
        return False
    
    def rotate_key(self, old_key_id: str = "default", new_key_id: str = "default_v2") -> Optional[bytes]:
        """Rotate an encryption key."""
        old_key = self.get_key(old_key_id)
        if old_key is None:
            return None
        
        new_key = self.generate_key(new_key_id)
        self.delete_key(old_key_id)
        return new_key
    
    def create_encryption_instance(self, key_id: str = "default") -> Optional[AEADEncryption]:
        """Create an AEADEncryption instance using a stored key."""
        key = self.get_key(key_id)
        if key:
            return AEADEncryption(key=key)
        return None
