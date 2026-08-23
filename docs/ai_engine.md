# AI Engine Module for Personal AI System

This module provides AI capabilities for the Personal AI System, integrating local LLMs with encrypted data vault.

## Features

- **Ollama Integration**: Connect to local Ollama instance for LLM inference
- **Embeddings Generation**: Uses `nomic-embed-text` model for text embeddings
- **RAG Engine**: Retrieval-Augmented Generation with ChromaDB vector storage
- **Chat Engine**: Queries encrypted data vault using Llama-3.2 model
- **Security**: Full integration with existing AES-256-GCM encryption

## Dependencies

See `requirements.txt` for full list:
- `ollama` - Ollama Python client
- `llama-index` - RAG framework
- `chromadb` - Vector database

## Installation

```bash
pip install -r requirements.txt
ollama serve  # Start local Ollama in another terminal
```

## Setup

1. Pull required models:
```bash
ollama pull llama3.2
ollama pull nomic-embed-text
```

2. Ensure encryption key is available:
```python
from src.security.encryption import AEADEncryption
encryption_key = AEADEncryption().key  # Loads existing key or creates new one
```

## Usage

### Quick Start

```python
from src.ai_engine.chat_engine import ChatEngine
from src.security.encryption import AEADEncryption

# Initialize chat engine with encryption key
encryption_key = AEADEncryption().key
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
client.model_exists("llama-3.2")  # Check if model exists
client.generate("llama-3.2", "Hello")  # Generate text
client.chat("llama-3.2", [{"role": "user", "content": "Hi"}])  # Chat
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
results = rag.retrieve("search query")
```

### Complete Example

```python
from src.ai_engine.chat_engine import ChatEngine
from src.security.encryption import AEADEncryption

# Initialize
encryption_key = AEADEncryption().key
chat = ChatEngine(encryption_key=encryption_key)

# Store sensitive data securely
chat.add_data_to_vault("password_entry", {
    "url": "example.com",
    "username": "user123",
    "password": "secret123"
})

# Query with context
result = chat.query_vault("What are my credentials for example.com?")
print(result["response"])
```

## Error Handling

The module handles several error conditions:

- **Ollama not running**: `OllamaClientError` - Ensure `ollama serve` is running
- **Model not pulled**: `OllamaClientError` - Pull the model: `ollama pull <model>`
- **Vault encryption errors**: `DataVaultError` - Ensure encryption key is provided
- **RAG initialization errors**: `RAGEngineError` - Ensure ChromaDB is installed

## API Reference

### ChatEngine

- `initialize_rag()` - Build RAG index from vault data
- `query_vault(query, use_rag=True)` - Query vault with context
- `add_data_to_vault(key, data)` - Store encrypted data
- `clear_vault()` - Remove all data

### OllamaClient

- `list_models()` - Get available models
- `model_exists(name)` - Check model availability
- `generate(model, prompt)` - Text generation
- `chat(model, messages)` - Chat completion
- `embeddings(model, prompt)` - Generate embeddings

## Security Notes

- All data in the vault is encrypted with AES-256-GCM
- Encryption keys are managed by `src.security.encryption.AEADEncryption`
- Data is decrypted only during query processing
- RAG index stores plaintext embeddings (consider encryption if needed)

## Testing

```bash
# Run the demo
python examples/ai_engine_demo.py
```

## Troubleshooting

1. **"Cannot connect to Ollama"**: Start Ollama server: `ollama serve`
2. **"Model not found"**: Pull the model: `ollama pull llama3.2`
3. **Encryption errors**: Ensure `encryption_key` parameter is provided
4. **ChromaDB errors**: Install with `pip install chromadb`
