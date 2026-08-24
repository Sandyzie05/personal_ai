"""Central runtime configuration for the Personal AI System.

All model/host defaults live here (overridable via environment variables)
instead of being hardcoded in four different files. Set these in your shell
or a local .env before starting the app, e.g.:

    export PERSONAL_AI_CHAT_MODEL=qwen3:8b
    export PERSONAL_AI_EMBED_MODEL=nomic-embed-text
"""

import os

DEFAULT_OLLAMA_HOST = os.environ.get(
    "PERSONAL_AI_OLLAMA_HOST", "http://localhost:11434"
)
DEFAULT_CHAT_MODEL = os.environ.get("PERSONAL_AI_CHAT_MODEL", "qwen3:8b")
DEFAULT_EMBED_MODEL = os.environ.get("PERSONAL_AI_EMBED_MODEL", "nomic-embed-text")

# Rough context-window budget (characters, ~4 chars/token) used to guard
# against silently overflowing the model's context window when RAG context
# and chat history are concatenated into a single prompt.
DEFAULT_CONTEXT_WINDOW_TOKENS = int(
    os.environ.get("PERSONAL_AI_CONTEXT_WINDOW_TOKENS", "8192")
)
CHARS_PER_TOKEN_ESTIMATE = 4
