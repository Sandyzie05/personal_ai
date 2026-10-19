#!/usr/bin/env python3
"""Debug script to test OMLX connection issues."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ai_engine.omlx_client import OmlxClient
from src.config import PERSONAL_AI_OMLX_HOST, PERSONAL_AI_OMLX_CHAT_MODEL

def main():
    print("Testing OMLX Client Connection")
    print("=" * 40)

    # Create client with default settings
    client = OmlxClient(
        host=PERSONAL_AI_OMLX_HOST,
        model=PERSONAL_AI_OMLX_CHAT_MODEL
    )

    print(f"Host: {client.host}")
    print(f"Model: {client.model}")

    # Test connection
    print("\nTesting connection...")
    try:
        is_connected = client.check_connection()
        print(f"Connection status: {is_connected}")

        if is_connected:
            print("Connection successful!")
            print("Available models:")
            models = client.list_models()
            for model in models:
                print(f"  - {model}")
        else:
            print("Connection failed!")

    except Exception as e:
        print(f"Exception during connection test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()