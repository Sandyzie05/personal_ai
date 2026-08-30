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
