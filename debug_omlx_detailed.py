#!/usr/bin/env python3
"""More comprehensive debug script to test OMLX connection."""

import sys
import os
import traceback

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_omlx_import():
    """Test importing OMLX client"""
    print("Testing OMLX client import...")
    try:
        from src.ai_engine.omlx_client import OmlxClient
        print("✓ OmlxClient imported successfully")
        return True
    except Exception as e:
        print(f"✗ Failed to import OmlxClient: {e}")
        traceback.print_exc()
        return False

def test_omlx_connection():
    """Test OMLX connection with more details"""
    print("\nTesting OMLX connection...")
    try:
        from src.ai_engine.omlx_client import OmlxClient
        from src.config import PERSONAL_AI_OMLX_HOST, PERSONAL_AI_OMLX_CHAT_MODEL

        client = OmlxClient(
            host=PERSONAL_AI_OMLX_HOST,
            model=PERSONAL_AI_OMLX_CHAT_MODEL
        )

        print(f"Host: {client.host}")
        print(f"Model: {client.model}")

        # Test connection with detailed error info
        print("Attempting connection check...")
        result = client.check_connection()
        print(f"Connection result: {result}")

        if result:
            print("Connection successful!")
            try:
                models = client.list_models()
                print(f"Found {len(models)} models:")
                for model in models[:5]:  # Show first 5
                    print(f"  - {model}")
            except Exception as e:
                print(f"Error listing models: {e}")
        else:
            print("Connection failed!")

        return result

    except Exception as e:
        print(f"Error during connection test: {e}")
        traceback.print_exc()
        return False

def main():
    print("OMLX Debug Test")
    print("=" * 50)

    success = test_omlx_import()
    if success:
        test_omlx_connection()

    print("\nTest complete.")

if __name__ == "__main__":
    main()