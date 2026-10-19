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

# Engine selection for LLM inference (ollama|omlx)
PERSONAL_AI_LLM_ENGINE = os.environ.get("PERSONAL_AI_LLM_ENGINE", "ollama")
PERSONAL_AI_OMLX_HOST = os.environ.get("PERSONAL_AI_OMLX_HOST", "http://127.0.0.1:8000")
PERSONAL_AI_OMLX_CHAT_MODEL = os.environ.get("PERSONAL_AI_OMLX_CHAT_MODEL", "Qwen3.8-27B-4bit")
PERSONAL_AI_EMBED_ENGINE = os.environ.get("PERSONAL_AI_EMBED_ENGINE", "omlx")
PERSONAL_AI_OMLX_EMBED_MODEL = os.environ.get("PERSONAL_AI_OMLX_EMBED_MODEL", "mlx-community/Qwen3-VL-Embedding-2B-8bit")

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
# Embedding models have a fixed context window (nomic-embed-text's real
# trained context is 2048 tokens per `ollama show nomic-embed-text` - the
# 8192 figure some docs quote is just Ollama's `num_ctx` parameter, not the
# model's actual window). A single vault document larger than that makes
# Ollama return HTTP 500 ("input length exceeds the context length"), which
# aborts the whole RAG index build. Documents are split to this budget well
# under any embedding model's window so a large bill/PDF can no longer crash
# chat.
DEFAULT_EMBED_CHUNK_TOKENS = int(
    os.environ.get("PERSONAL_AI_EMBED_CHUNK_TOKENS", "1000")
)

# How many chunks from the *same* source document `ChromaStore.retrieve_relevant`
# keeps, at most. 1 (the old default) meant a multi-page bill/statement could
# only ever contribute its single best-scoring chunk - if the actual $ figure
# or line item lived in a lower-ranked chunk, it was silently dropped even
# when retrieved. Raised to 3 so a few of a document's best chunks can each
# still surface.
DEFAULT_MAX_PER_DOCUMENT = int(
    os.environ.get("PERSONAL_AI_MAX_CHUNKS_PER_DOCUMENT", "3")
)

# Overlap (chars) carried from the tail of one chunk into the start of the
# next when a single paragraph/line is long enough to need word-splitting
# (see chroma_store._chunk_text). Without this, a fact split right at the
# cut point (e.g. a label at the end of one chunk, its amount at the start
# of the next) could land in two chunks that never get retrieved together.
DEFAULT_CHUNK_OVERLAP_CHARS = int(
    os.environ.get("PERSONAL_AI_CHUNK_OVERLAP_CHARS", "200")
)

# Document text (chars) sent to the LLM for structured field extraction
# (src/data_extraction/extractor.py). Previously hardcoded to 6000 chars
# (~1500 tokens); long multi-page statements (e.g. a brokerage transaction
# history) got silently truncated before extraction, dropping trailing line
# items with no error. Sized to leave headroom in
# DEFAULT_CONTEXT_WINDOW_TOKENS for the prompt template, JSON schema, and
# ~2048 output tokens.
DEFAULT_EXTRACTION_TEXT_CHARS = int(
    os.environ.get("PERSONAL_AI_EXTRACTION_TEXT_CHARS", "20000")
)

# Context window tokens for different embedding models
EMBED_MODEL_CONTEXT_TOKENS = {
    "nomic-embed-text": 2048,
    "mlx-community/Qwen3-VL-Embedding-2B-8bit": 32768,
    "mlx-community/Qwen3-Embedding-0.6B": 8192
}

# Override the EMBED_MODEL_CONTEXT_TOKENS with environment variables if set
if "PERSONAL_AI_EMBED_MODEL_CONTEXT_TOKENS" in os.environ:
    try:
        # Parse environment variable as JSON dict
        import json
        EMBED_MODEL_CONTEXT_TOKENS.update(json.loads(os.environ["PERSONAL_AI_EMBED_MODEL_CONTEXT_TOKENS"]))
    except Exception:
        pass  # If parsing fails, use the defaults

# --- Multi-agent layer (see docs/multi_agent_design.md) ---

# Skills are plain instruction files (not personal data), so they live in a
# directory rather than the encrypted vault - same accepted tradeoff as the
# .chroma index. Override if you keep them elsewhere.
SKILLS_DIR = os.environ.get(
    "PERSONAL_AI_SKILLS_DIR", "~/.personal_ai/skills"
)

# Default cap on LLM<->tool round-trips per agent run (bounds latency and
# cost on a local model; an exhausted run ends with a notice, not a crash).
DEFAULT_AGENT_MAX_TOOL_ROUNDS = int(
    os.environ.get("PERSONAL_AI_AGENT_MAX_TOOL_ROUNDS", "6")
)

# How long the local OAuth callback listener waits for the user to finish
# authorizing a remote MCP server in the browser.
MCP_OAUTH_TIMEOUT_SECONDS = int(
    os.environ.get("PERSONAL_AI_MCP_OAUTH_TIMEOUT", "300")
)

# A tool's result is truncated to this many characters before it re-enters
# the model's context (a single tool can return large payloads - e.g. a
# whole order book - which would eat the context budget in one turn).
MCP_TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("PERSONAL_AI_MCP_TOOL_RESULT_MAX_CHARS", "8000")
)

# Forecaster adapters accept only fixed public-provider operations.
FORECAST_HTTP_TIMEOUT_SECONDS = 15.0
FORECAST_HTTP_MAX_BYTES = 4_000_000
FORECAST_CACHE_TTL_SECONDS = 300.0
FORECAST_CACHE_MAX_ENTRIES = 64
FORECAST_RESEARCH_DEADLINE_SECONDS = 45.0
FORECAST_SCENARIO_DEADLINE_SECONDS = 60.0
FORECAST_MODEL_CALL_BUDGET = 8
FORECAST_MODEL_MAX_TOKENS = 400
FORECAST_SNAPSHOT_MAX_CHARS = 18000
FORECAST_MODEL_OUTPUT_MAX_CHARS = 5000
FORECAST_SEC_USER_AGENT = os.environ.get("PERSONAL_AI_SEC_USER_AGENT", "")
FORECAST_FRED_API_KEY = os.environ.get("PERSONAL_AI_FRED_API_KEY", "")
FORECAST_ALPHA_VANTAGE_API_KEY = os.environ.get("PERSONAL_AI_ALPHA_VANTAGE_API_KEY", "")
