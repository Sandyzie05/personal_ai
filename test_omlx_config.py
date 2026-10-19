#!/usr/bin/env python3

"""Debug script to check the oMLX configuration and client setup"""

import os
import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

print("Environment variables:")
for key in sorted(os.environ.keys()):
    if 'PERSONAL_AI' in key or 'OMLX' in key or 'OLLAMA' in key:
        print(f"  {key}: {os.environ[key]}")

from src.config import (
    PERSONAL_AI_LLM_ENGINE,
    PERSONAL_AI_EMBED_ENGINE,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_CHAT_MODEL,
    PERSONAL_AI_OMLX_EMBED_MODEL
)

print("\nConfiguration values:")
print(f"LLM Engine: {PERSONAL_AI_LLM_ENGINE}")
print(f"Embed Engine: {PERSONAL_AI_EMBED_ENGINE}")
print(f"OMLX Host: {PERSONAL_AI_OMLX_HOST}")
print(f"OMLX Chat Model: {PERSONAL_AI_OMLX_CHAT_MODEL}")
print(f"OMLX Embed Model: {PERSONAL_AI_OMLX_EMBED_MODEL}")