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
DEFAULT_CHAT_MODEL = os.environ.get("PERSONAL_AI_CHAT_MODEL", "llama3.2:latest")
DEFAULT_EMBED_MODEL = os.environ.get("PERSONAL_AI_EMBED_MODEL", "nomic-embed-text")

# Default generation temperature for grounded QA. 0.7 (the old default) is too
# free-wheeling for an assistant that must answer strictly from retrieved
# context: higher temperature makes a small local model more likely to
# confabulate. 0.3 keeps replies readable while staying firmly grounded.
DEFAULT_CHAT_TEMPERATURE = float(os.environ.get("PERSONAL_AI_CHAT_TEMPERATURE", "0.3"))

# Minimum cosine SIMILARITY for a retrieved chunk to count as relevant.
# ChromaDB's "cosine" space returns a *distance* (1 - similarity, range
# [0, 2]); a chunk is kept only when its similarity >= this threshold. This is
# the single biggest anti-hallucination lever: without it, the top-k query
# always returns k chunks - even nearly-unrelated ones (similarity ~0) - which
# the model then has no excuse to answer from. Set to 0.0 to disable.
DEFAULT_MIN_RELATIVE_SCORE = float(
    os.environ.get("PERSONAL_AI_MIN_RELATIVE_SCORE", "0.20")
)

# Rough context-window budget (characters, ~4 chars/token) used to guard
# against silently overflowing the model's context window when RAG context
# and chat history are concatenated into a single prompt.
DEFAULT_CONTEXT_WINDOW_TOKENS = int(
    os.environ.get("PERSONAL_AI_CONTEXT_WINDOW_TOKENS", "8192")
)
CHARS_PER_TOKEN_ESTIMATE = 4

# Per-chunk budget (tokens) when splitting documents before embedding.
#
# Embedding models have a fixed context window (nomic-embed-text is 8192
# tokens). A single vault document larger than that makes Ollama return HTTP
# 500 ("input length exceeds the context length"), which aborts the whole RAG
# index build. Documents are split to this budget well under any embedding
# model's window so a large bill/PDF can no longer crash chat.
DEFAULT_EMBED_CHUNK_TOKENS = int(
    os.environ.get("PERSONAL_AI_EMBED_CHUNK_TOKENS", "1000")
)
