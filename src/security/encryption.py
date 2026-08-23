"""
AES-256-GCM Encryption Utilities for Personal AI System
"""
import os
from typing import Tuple, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import base64


class EncryptionError(Exception):
    pass


class AEADEncryption:
    KEY_SIZE = 32
    NONCE_SIZE = 12
    
    def __init__(self, key: bytes = None, key_path: str = None):
        self.key_path = key_path or os.path.expanduser("~/.personal_ai_key")
        self.backend = default_backend()
        if key:
            if len(key) != self.KEY_SIZE:
                raise ValueError(f"Key must be {self.KEY_SIZE} bytes")
            self.key = key
        else:
            self.key = self._load_or_create_key()
    
    def _load_or_create_key(self) -> bytes:
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                return f.read()
        else:
            key = os.urandom(self.KEY_SIZE)
            with open(self.key_path, "wb") as f:
                f.write(key)
            os.chmod(self.key_path, 0o600)
            return key
    
    def _generate_nonce(self) -> bytes:
        return os.urandom(self.NONCE_SIZE)
    
    def encrypt(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        try:
            nonce = self._generate_nonce()
            aesgcm = AESGCM(self.key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)
            return nonce + ciphertext
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {str(e)}")
    
    def decrypt(self, encrypted_data: bytes, associated_data: bytes = b"") -> bytes:
        try:
            if len(encrypted_data) < self.NONCE_SIZE:
                raise EncryptionError("Encrypted data too short")
            nonce = encrypted_data[:self.NONCE_SIZE]
            ciphertext = encrypted_data[self.NONCE_SIZE:]
            aesgcm = AESGCM(self.key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)
            return plaintext
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {str(e)}")
    
    def encrypt_string(self, plaintext: str) -> str:
        encrypted = self.encrypt(plaintext.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")
    
    def decrypt_string(self, encrypted_string: str) -> str:
        encrypted = base64.b64decode(encrypted_string.encode("utf-8"))
        plaintext = self.decrypt(encrypted)
        return plaintext.decode("utf-8")


class KeyManager:
    def __init__(self, service_name: str = "personal_ai"):
        self.service_name = service_name
        self.keyring = None
        try:
            import keyring
            self.keyring = keyring
        except ImportError:
            self.keyring = None
    
    def store_key(self, key_id: str, key: bytes) -> bool:
        if self.keyring:
            try:
                self.keyring.set_password(self.service_name, key_id, base64.b64encode(key).decode("utf-8"))
                return True
            except Exception:
                pass
        key_path = os.path.expanduser(f"~/.personal_ai_{key_id}_key")
        with open(key_path, "wb") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
        return True
    
    def get_key(self, key_id: str) -> Optional[bytes]:
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


class PasswordKeyDerivation:
    SALT_SIZE = 16
    ITERATIONS = 210000
    
    @staticmethod
    def derive_key(password: str, salt: bytes = None) -> Tuple[bytes, bytes]:
        if salt is None:
            salt = os.urandom(PasswordKeyDerivation.SALT_SIZE)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=PasswordKeyDerivation.ITERATIONS,
            backend=default_backend()
        )
        key = kdf.derive(password.encode("utf-8"))
        return key, salt
    
    @staticmethod
    def encrypt_with_password(plaintext: bytes, password: str) -> dict:
        salt, key = PasswordKeyDerivation.derive_key(password)
        encryption = AEADEncryption(key=key)
        encrypted = encryption.encrypt(plaintext)
        return {
            "encrypted_data": base64.b64encode(encrypted).decode("utf-8"),
            "salt": base64.b64encode(salt).decode("utf-8"),
            "iterations": PasswordKeyDerivation.ITERATIONS
        }
    
    @staticmethod
    def decrypt_with_password(encrypted_data: str, password: str, salt: str, iterations: int = None) -> bytes:
        if iterations is None:
            iterations = PasswordKeyDerivation.ITERATIONS
        encrypted = base64.b64decode(encrypted_data)
        salt_bytes = base64.b64decode(salt)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt_bytes,
            iterations=iterations,
            backend=default_backend()
        )
        key = kdf.derive(password.encode("utf-8"))
        encryption = AEADEncryption(key=key)
        return encryption.decrypt(encrypted)


def encrypt_data(plaintext: bytes, key: bytes = None) -> bytes:
    encryption = AEADEncryption(key=key)
    return encryption.encrypt(plaintext)


def decrypt_data(encrypted: bytes, key: bytes = None) -> bytes:
    encryption = AEADEncryption(key=key)
    return encryption.decrypt(encrypted)


def encrypt_string(plaintext: str, key: bytes = None) -> str:
    encryption = AEADEncryption(key=key)
    return encryption.encrypt_string(plaintext)


def decrypt_string(encrypted: str, key: bytes = None) -> str:
    encryption = AEADEncryption(key=key)
    return encryption.decrypt_string(encrypted)
