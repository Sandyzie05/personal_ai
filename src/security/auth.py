"""
Authentication Module for Personal AI System
"""
import os
import getpass
import hmac
import hashlib
import base64
import json


class AuthenticationError(Exception):
    pass


class AuthManager:
    def __init__(self):
        self.auth_file = os.path.expanduser("~/.personal_ai_auth")
        self.max_attempts = 3
    
    def _hash_password(self, password, salt):
        password_bytes = password.encode("utf-8")
        hashed = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, 100000)
        return base64.b64encode(hashed).decode("utf-8")
    
    def register(self, password=None):
        if os.path.exists(self.auth_file):
            print("User already registered.")
            return False
        
        if password is None:
            password = getpass.getpass("Enter password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Passwords do not match.")
                return False
        
        salt = os.urandom(16)
        hashed = self._hash_password(password, salt)
        
        auth_data = {
            "salt": base64.b64encode(salt).decode("utf-8"),
            "password_hash": hashed
        }
        
        with open(self.auth_file, "w") as f:
            json.dump(auth_data, f)
        os.chmod(self.auth_file, 0o600)
        
        print("Registration successful!")
        return True
    
    def authenticate(self, password=None):
        if not os.path.exists(self.auth_file):
            print("No user registered. Please register first.")
            return False
        
        with open(self.auth_file, "r") as f:
            auth_data = json.load(f)
        
        if password is None:
            password = getpass.getpass("Enter password: ")
        
        salt = base64.b64decode(auth_data["salt"])
        stored_hash = auth_data["password_hash"]
        current_hash = self._hash_password(password, salt)
        
        if hmac.compare_digest(current_hash, stored_hash):
            print("Authentication successful!")
            return True
        else:
            print("Authentication failed.")
            return False
    
    def authenticate_interactive(self):
        for attempt in range(self.max_attempts):
            if self.authenticate():
                return True
            if attempt < self.max_attempts - 1:
                print(f"Attempts remaining: {self.max_attempts - attempt - 1}")
        print("Maximum authentication attempts exceeded.")
        return False


def main():
    auth = AuthManager()
    print("Personal AI Authentication System")
    print("=" * 40)
    
    if os.path.exists(auth.auth_file):
        print("Existing user detected.")
        action = input("Login? (y/n): ").lower()
        if action == "y":
            if auth.authenticate_interactive():
                print("Access granted.")
            else:
                print("Access denied.")
    else:
        print("New user registration.")
        action = input("Register? (y/n): ").lower()
        if action == "y":
            auth.register()


if __name__ == "__main__":
    main()
