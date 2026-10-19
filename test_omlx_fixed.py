#!/usr/bin/env python3
"""Test script to verify OMLX client with API key works."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_omlx_client():
    """Test OMLX client with API key handling."""
    print("Testing OMLX Client with API Key")
    print("=" * 40)

    # Set the API key in environment for testing
    os.environ['omlx_api_key'] = 'sk-omlx-K6TpOdUluM6MtzY1OznwE10U'

    try:
        from src.ai_engine.omlx_client import OmlxClient
        from src.config import PERSONAL_AI_OMLX_HOST, PERSONAL_AI_OMLX_CHAT_MODEL

        print(f"Creating OmlxClient with host: {PERSONAL_AI_OMLX_HOST}")
        print(f"Model: {PERSONAL_AI_OMLX_CHAT_MODEL}")

        client = OmlxClient(
            host=PERSONAL_AI_OMLX_HOST,
            model=PERSONAL_AI_OMLX_CHAT_MODEL
        )

        print(f"API Key set: {bool(client.api_key)}")
        if client.api_key:
            print(f"API Key (first 10 chars): {client.api_key[:10]}...")

        # Test connection
        print("\nTesting connection...")
        result = client.check_connection()
        print(f"Connection result: {result}")

        if result:
            print("✓ OMLX client works correctly with API key")
            try:
                models = client.list_models()
                print(f"✓ Found {len(models)} models")
                for model in models[:3]:  # Show first 3
                    print(f"  - {model}")
            except Exception as e:
                print(f"Note: Could not list models: {e}")
        else:
            print("✗ Connection failed")

    except Exception as e:
        print(f"✗ Error during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_omlx_client()