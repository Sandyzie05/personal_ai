"""
Authentication Module for Personal AI System.

Owns the lifecycle of the vault's data-encryption key (DEK):
  - register(password): generates a random DEK, wraps it with a key derived
    from the password (PBKDF2-HMAC-SHA256), and persists only the wrapped
    form. The plaintext DEK is never written to disk.
  - authenticate(password): re-derives the wrapping key from the password
    and attempts to AEAD-decrypt the wrapped DEK. A wrong password fails the
    AEAD authentication tag check, which *is* the password check - there is
    no separate password hash to keep in sync.

Failed attempts are persisted (not just tracked in-process) so a script
cannot bypass the lockout by calling authenticate() in a fresh process.
"""

import os
import getpass
import base64
import json
import time
from typing import Optional

from .encryption import AEADEncryption, EncryptionError, PasswordKeyDerivation


class AuthenticationError(Exception):
    pass


class AuthManager:
    MAX_ATTEMPTS = 5
    LOCKOUT_SECONDS = 300

    def __init__(self, auth_file: Optional[str] = None):
        self.auth_file = auth_file or os.path.expanduser("~/.personal_ai_auth")
        self.max_attempts = self.MAX_ATTEMPTS

    def is_registered(self) -> bool:
        return os.path.exists(self.auth_file)

    def _read_state(self) -> dict:
        with open(self.auth_file, "r") as f:
            return json.load(f)

    def _write_state(self, state: dict) -> None:
        with open(self.auth_file, "w") as f:
            json.dump(state, f)
        os.chmod(self.auth_file, 0o600)

    def register(self, password: Optional[str] = None) -> bytes:
        """Create a new account, generate a DEK, and wrap it with the password.

        Returns the plaintext DEK to use for the current session.
        """
        if self.is_registered():
            raise AuthenticationError("User already registered.")

        if password is None:
            password = getpass.getpass("Enter password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise AuthenticationError("Passwords do not match.")
        if len(password) < 8:
            raise AuthenticationError("Password must be at least 8 characters.")

        dek = os.urandom(AEADEncryption.KEY_SIZE)
        kek, salt = PasswordKeyDerivation.derive_key(password)
        wrapped_dek = AEADEncryption(key=kek).encrypt(dek)

        state = {
            "version": 2,
            "salt": base64.b64encode(salt).decode("utf-8"),
            "iterations": PasswordKeyDerivation.ITERATIONS,
            "wrapped_key": base64.b64encode(wrapped_dek).decode("utf-8"),
            "failed_attempts": 0,
            "locked_until": 0,
        }
        self._write_state(state)
        return dek

    def _check_lockout(self, state: dict) -> None:
        locked_until = state.get("locked_until", 0)
        if locked_until and time.time() < locked_until:
            remaining = int(locked_until - time.time())
            raise AuthenticationError(
                f"Too many failed attempts. Try again in {remaining} seconds."
            )

    def _record_failure(self, state: dict) -> None:
        state["failed_attempts"] = state.get("failed_attempts", 0) + 1
        if state["failed_attempts"] >= self.max_attempts:
            state["locked_until"] = time.time() + self.LOCKOUT_SECONDS
            state["failed_attempts"] = 0
        self._write_state(state)

    def _record_success(self, state: dict) -> None:
        state["failed_attempts"] = 0
        state["locked_until"] = 0
        self._write_state(state)

    def authenticate(self, password: Optional[str] = None) -> bytes:
        """Verify the password and return the unwrapped DEK.

        Raises AuthenticationError on any failure (no account, locked out,
        or wrong password).
        """
        if not self.is_registered():
            raise AuthenticationError("No user registered. Please register first.")

        state = self._read_state()
        self._check_lockout(state)

        if password is None:
            password = getpass.getpass("Enter password: ")

        salt = base64.b64decode(state["salt"])
        iterations = state.get("iterations", PasswordKeyDerivation.ITERATIONS)
        wrapped_dek = base64.b64decode(state["wrapped_key"])

        kek, _ = PasswordKeyDerivation.derive_key(password, salt=salt)
        # Iterations may differ if this account predates a bump; rederive if so.
        if iterations != PasswordKeyDerivation.ITERATIONS:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.backends import default_backend

            kek = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=iterations,
                backend=default_backend(),
            ).derive(password.encode("utf-8"))

        try:
            dek = AEADEncryption(key=kek).decrypt(wrapped_dek)
        except EncryptionError:
            self._record_failure(state)
            raise AuthenticationError("Authentication failed.")

        self._record_success(state)
        return dek

    def authenticate_interactive(self) -> Optional[bytes]:
        for attempt in range(self.max_attempts):
            try:
                return self.authenticate()
            except AuthenticationError as e:
                print(str(e))
                if attempt < self.max_attempts - 1:
                    print(f"Attempts remaining: {self.max_attempts - attempt - 1}")
        print("Maximum authentication attempts exceeded.")
        return None

    def change_password(self, old_password: str, new_password: str) -> bytes:
        """Re-wrap the existing DEK under a new password. Returns the DEK."""
        dek = self.authenticate(old_password)
        if len(new_password) < 8:
            raise AuthenticationError("Password must be at least 8 characters.")

        kek, salt = PasswordKeyDerivation.derive_key(new_password)
        wrapped_dek = AEADEncryption(key=kek).encrypt(dek)
        state = self._read_state()
        state["salt"] = base64.b64encode(salt).decode("utf-8")
        state["iterations"] = PasswordKeyDerivation.ITERATIONS
        state["wrapped_key"] = base64.b64encode(wrapped_dek).decode("utf-8")
        self._write_state(state)
        return dek


def main():
    auth = AuthManager()
    print("Personal AI Authentication System")
    print("=" * 40)

    if auth.is_registered():
        print("Existing user detected.")
        action = input("Login? (y/n): ").lower()
        if action == "y":
            dek = auth.authenticate_interactive()
            print("Access granted." if dek else "Access denied.")
    else:
        print("New user registration.")
        action = input("Register? (y/n): ").lower()
        if action == "y":
            try:
                auth.register()
                print("Registration successful!")
            except AuthenticationError as e:
                print(str(e))


if __name__ == "__main__":
    main()
