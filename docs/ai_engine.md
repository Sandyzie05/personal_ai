# AI Engine Module for Personal AI System

This module provides AI capabilities for the Personal AI System, integrating local LLMs with encrypted data vault.

## Features

- **Ollama Integration**: Connect to local Ollama instance for LLM inference
- **Embeddings Generation**: Configurable Ollama or oMLX embedding backend
- **RAG Engine**: Retrieval-Augmented Generation with ChromaDB vector storage
- **Chat Engine**: Queries encrypted data vault using the configured chat model
- **Security**: Full integration with existing AES-256-GCM encryption

## Dependencies

See `requirements.txt` for full list:
- `ollama` - Ollama Python client
- `chromadb` - Vector database

RAG is implemented in this repository, not through LlamaIndex. Backend,
host, model and context defaults come from `src/config.py`; saved Settings
configure the application. The code defaults to Ollama chat and oMLX
embeddings, so an Ollama-only setup must select Ollama embeddings explicitly.
Chat and agent context builders share the saved embedding configuration.
Collections are namespaced by the actual embedding backend, host and model;
changing dimensions does not reuse an incompatible collection. Explicit
rebuilds replace the active index from vault records; normal document
mutations replace/remove only that source's chunks.

## Installation

```bash
pip install -r requirements.txt
ollama serve  # Start local Ollama in another terminal
```

## Setup

1. For an Ollama-only setup, select its embedding backend and install only
the configured models that are missing:
```bash
export PERSONAL_AI_EMBED_ENGINE=ollama
ollama pull llama3.2:latest
ollama pull nomic-embed-text
```

2. Use the application's registration/login flow. `AuthManager` is the only
supported password-to-DEK path. Do not construct `AEADEncryption()` without
an explicit key to bootstrap the application; its legacy raw-key helper is
not the supported auth lifecycle.

## Usage

### Quick Start

```python
from src.ai_engine.chat_engine import ChatEngine
from getpass import getpass
from src.security.auth import AuthManager

# Use an already registered vault; never put a password in source code.
encryption_key = AuthManager().authenticate(getpass("Vault password: "))
chat_engine = ChatEngine(encryption_key=encryption_key)

# Add data to encrypted vault
chat_engine.add_data_to_vault("my_note", {
    "title": "Important Note",
    "content": "This is my sensitive data"
})

# Initialize RAG with vault data
chat_engine.initialize_rag()

# Query the vault
result = chat_engine.query_vault("What is in my vault?")
print(result["response"])
```

### Low-Level APIs

#### Ollama Client

```python
from src.ai_engine.ollama_client import OllamaClient

client = OllamaClient()
client.list_models()  # List available models
client.model_exists(client.model)  # Check configured model
client.chat([{"role": "user", "content": "Hi"}])
```

#### Embeddings Generator

```python
from src.ai_engine.embeddings import EmbeddingsGenerator

generator = EmbeddingsGenerator()
embedding = generator.generate_embedding("Hello world")
```

#### RAG Engine

```python
from src.ai_engine.rag_engine import RAGEngine

rag = RAGEngine()
rag.add_document("Some text to index")
results = rag.query("search query")
```

The examples use the configured model endpoints and an existing vault.
For experiments, use an isolated HOME and synthetic records rather than
real credentials or personal documents. Do not put passwords in chat,
documents, source code or diagnostic output.

## Error Handling

The module handles several error conditions:

- **Ollama not running**: `OllamaClientError` - Ensure `ollama serve` is running
- **Model not pulled**: `OllamaClientError` - Pull the model: `ollama pull <model>`
- **Vault encryption errors**: `DataVaultError` - Ensure encryption key is provided
- **RAG initialization errors**: `RAGEngineError` - Ensure ChromaDB is installed

## API Reference

### ChatEngine

- `initialize_rag()` - Build RAG index from vault data
- `query_vault(query, use_rag=True, history=None)` - Query vault with
  context; `history` is an optional list of `{"role": ..., "content": ...}`
  dicts (oldest first, not including `query` itself) giving prior
  conversation turns - it's budget-fitted (oldest dropped first) alongside
  RAG/structured context so the combined prompt still fits the model's
  context window
- `query_vault_stream(query, use_rag=True, history=None)` - Same as
  `query_vault`, but returns a `StreamingChatResponse` (iterate for text
  deltas; `.sources` is available immediately)
- `estimate_usage(history, pending_context="")` - Pure token-budget estimate
  (no vault/RAG/Ollama calls) used to drive a context-window usage meter in
  the UI; returns `{"used_tokens", "budget_tokens", "ratio"}`
- `add_data_to_vault(key, data)` - Store encrypted data
- `clear_vault()` - Remove all data

### OllamaClient

- `OllamaClient(host=..., model=..., embed_model=..., temperature=..., max_tokens=..., context_window_tokens=...)` -
  `context_window_tokens` (defaults from `PERSONAL_AI_CONTEXT_WINDOW_TOKENS`
  in `src/config.py`) is passed as `num_ctx` on every `chat()` call, so the
  app's own context-budget assumptions and what Ollama is actually told to
  use can't drift apart
- `list_models()` - Get available models
- `model_exists(name)` - Check model availability
- `chat(messages, stream=False, format=None)` - Chat completion (sends
  `temperature`/`num_predict`/`num_ctx` as Ollama options)
- `generate_embedding(text)` - Generate an embedding for a text string

## Security Notes

- All data in the vault is encrypted with AES-256-GCM
- AuthManager owns DEK creation/wrapping/unwrapping; encryption helpers
  receive the resulting key explicitly
- Decrypted data exists during ingestion, indexing, UI rendering and queries
- Chroma stores plaintext document text, embeddings and metadata; its
  directory is private, not encrypted
- Model endpoints are configurable, not universally restricted to localhost;
  use trusted local endpoints for private inference
- MCP agents can send model-selected tool arguments to configured remote
  servers; disabling RAG does not remove private user/history text
- See [security_design.md](security_design.md) for residual risks

## Testing

```bash
# Offline regressions (use isolated storage; see development plan)
make test
```

`make demo` is a live auth/vault/model smoke flow, not an offline test.
Run `make check-context` after chat-path/model changes; an unavailable
configured model/service is an explicit integration-check blocker.

## Troubleshooting

1. **"Cannot connect to Ollama"**: Start Ollama server: `ollama serve`
2. **"Model not found"**: Check the selected backend's installed models,
   then install the explicitly configured missing model
3. **Encryption errors**: Ensure `encryption_key` parameter is provided
4. **ChromaDB errors**: Install with `pip install chromadb`
