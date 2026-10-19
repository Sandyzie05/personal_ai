#!/usr/bin/env python3
"""Main debugging script to check the configuration and find the root cause."""

import sys
import os
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

print("=== DEBUGGING THE OMLX CONFIGURATION ISSUE ===")
print()

# Print environment variables
print("Environment variables relevant to our configuration:")
env_vars = [
    'PERSONAL_AI_LLM_ENGINE',
    'PERSONAL_AI_EMBED_ENGINE',
    'PERSONAL_AI_OMLX_HOST',
    'PERSONAL_AI_OMLX_CHAT_MODEL',
    'PERSONAL_AI_OMLX_EMBED_MODEL',
    'omlx_api_key',
    'OMLX_API_KEY'
]

for var in env_vars:
    value = os.environ.get(var, 'NOT SET')
    if 'key' in var.lower():
        # Mask API keys for security
        if value != 'NOT SET':
            value = value[:10] + '...' if len(value) > 10 else 'MASKED'
    print(f"  {var}: {value}")

print()

# Check configuration loading
print("=== CONFIGURATION LOADING ===")
try:
    from src.config import (
        PERSONAL_AI_LLM_ENGINE,
        PERSONAL_AI_EMBED_ENGINE,
        PERSONAL_AI_OMLX_HOST,
        PERSONAL_AI_OMLX_CHAT_MODEL,
        PERSONAL_AI_OMLX_EMBED_MODEL
    )

    print(f"LLM Engine: {PERSONAL_AI_LLM_ENGINE}")
    print(f"Embed Engine: {PERSONAL_AI_EMBED_ENGINE}")
    print(f"OMLX Host: {PERSONAL_AI_OMLX_HOST}")
    print(f"OMLX Chat Model: {PERSONAL_AI_OMLX_CHAT_MODEL}")
    print(f"OMLX Embed Model: {PERSONAL_AI_OMLX_EMBED_MODEL}")
    print()

    # Test the build functions
    print("=== BUILDING CLIENTS ===")
    from src.ai_engine.engine import build_chat_client, build_embed_client

    # Build chat client with OMLX
    try:
        chat_client = build_chat_client(engine='omlx')
        print(f"✓ Chat client built for OMLX")
        print(f"  Host: {chat_client.host}")
        print(f"  Model: {chat_client.model}")
        if hasattr(chat_client, 'api_key') and chat_client.api_key:
            print(f"  API Key: {'*' * len(chat_client.api_key) if chat_client.api_key else 'None'}")
    except Exception as e:
        print(f"✗ Failed to build chat client for OMLX: {e}")

    # Build embed client with OMLX
    try:
        embed_client = build_embed_client(engine='omlx')
        print(f"✓ Embed client built for OMLX")
        print(f"  Host: {embed_client.host}")
        print(f"  Model: {embed_client.model}")
    except Exception as e:
        print(f"✗ Failed to build embed client for OMLX: {e}")

    # Test direct client initialization
    print()
    print("=== DIRECT CLIENT TESTING ===")
    try:
        from src.ai_engine.omlx_client import OmlxClient
        client = OmlxClient(
            host=PERSONAL_AI_OMLX_HOST,
            model=PERSONAL_AI_OMLX_CHAT_MODEL
        )
        print(f"✓ Direct OmlxClient created")
        print(f"  Host: {client.host}")
        print(f"  Model: {client.model}")
        print(f"  API Key: {'*' * len(client.api_key) if client.api_key else 'None'}")

        # Test connection (but don't make actual request since we don't know if server is running)
        print(f"  Connection check available: {hasattr(client, 'check_connection')}")

    except Exception as e:
        print(f"✗ Direct OmlxClient creation failed: {e}")
        import traceback
        traceback.print_exc()

except Exception as e:
    print(f"✗ Configuration loading error: {e}")
    import traceback
    traceback.print_exc()

print()
print("=== SUMMARY ===")
print("If you're still seeing Ollama errors, ensure:")
print("1. The oMLX server is actually running on the configured host")
print("2. The oMLX API key is correctly set")
print("3. The model names are correct for the oMLX server")
print("4. Your application configuration is correctly loaded")