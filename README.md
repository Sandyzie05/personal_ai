# 🛡️ Secure Personal AI System

A personal AI assistant that answers questions about your own data (notes,
bills, health/financial documents, etc.) using open-source LLMs running
**entirely locally** via [Ollama](https://ollama.com), with an encrypted
vault for everything you give it.

**Core principle**: your data never leaves your machine. All AI processing
- chat, embeddings, retrieval - happens locally.

## Features

- 🔒 Password-gated encrypted vault (AES-256-GCM, key derived from your
  password via PBKDF2 - see `docs/security_design.md`)
- 💬 Local chat over your vault data (RAG) using any Ollama chat model
- 📂 File ingestion: PDF, DOCX, CSV, Apple Health XML, plus a T-Mobile bill
  extractor (`src/data_extraction/`, see `BILL_EXTRACTION_README.md`)
- 🖥️ Streamlit web UI with chat, upload, file browser, and settings pages

## Project structure

```
src/
├── security/        # Auth (password -> vault key), AES-256-GCM encryption
├── data_vault/       # Encrypted SQLite storage
├── ai_engine/        # Ollama client, RAG engine, ChromaDB vector store
├── data_ingestion/    # File upload handling + per-format parsers
├── data_extraction/   # Structured bill extraction (pattern-based)
├── interface/         # Streamlit UI
└── config.py           # Central model/host/context-window configuration

scripts/               # Standalone tools (e.g. context-overflow checker)
docs/                  # Security design, roadmap/history
examples/              # CLI usage examples
```

## Requirements

- macOS, Linux, or Windows
- Python 3.10+
- [Ollama](https://ollama.com/download), installed and running (`ollama serve`)

## Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd personal_ai

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate

# 3. Install dependencies
make install   # or: pip install -r requirements.txt

# 4. Pull the models you'll use (defaults below; override via env vars, see Configuration)
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

## Running locally

```bash
make run   # or: PYTHONPATH=. streamlit run src/interface/main.py
```

Open http://localhost:8501. On first run you'll be asked to set a vault
password - this both gates the UI and derives the encryption key for
everything you store, so **there is no recovery if you forget it** (no
backdoor, no reset flow yet). Subsequent runs prompt for that same password.

Other useful entry points:

```bash
make demo           # CLI walkthrough: register/login, store data, RAG query
make check-context  # Sanity-check model context-window assumptions (see below)
make test           # Run the test suite
```

## Configuration

Model names and the Ollama host are centralized in `src/config.py` and
overridable via environment variables instead of editing code:

```bash
export PERSONAL_AI_OLLAMA_HOST=http://localhost:11434
export PERSONAL_AI_CHAT_MODEL=qwen3:8b
export PERSONAL_AI_EMBED_MODEL=nomic-embed-text
export PERSONAL_AI_CONTEXT_WINDOW_TOKENS=8192   # must not exceed your chat model's real window
```

Run `make check-context` after changing models to confirm the configured
context window doesn't exceed what Ollama actually reports for that model -
this catches silent truncation/overflow before it shows up as a bad answer
or a runtime error.

## Security

The vault is encrypted at rest (AES-256-GCM) with a key derived from your
password; the key is never written to disk in plaintext, and repeated wrong
passwords trigger a persistent lockout. The one known residual risk: the
local semantic search index (ChromaDB) stores document text in plaintext
under `~/.personal_ai_vault/.chroma` (locked to `0700` permissions) to
support fast local search - it is not yet encrypted at rest.

Full threat model, what's implemented vs. not, and why: **`docs/security_design.md`**.

## Documentation

- `docs/security_design.md` - security architecture, threat model, residual risks
- `docs/roadmap.md` - original project plan and feature roadmap (historical; not current state)
- `AGENTS.md` - checklist and conventions for making further changes to this repo
- `INTERFACE_README.md` / `INTERFACE_SUMMARY.md` - Streamlit UI notes
- `BILL_EXTRACTION_README.md` - T-Mobile bill extraction feature notes

## Contributing & Maintenance

This is a personal project, but contributions and suggestions are welcome.
Always prioritize security over features - see `AGENTS.md` before changing
anything under `src/security/` or `src/data_vault/`.

## License

This project is proprietary. All code and data are private and confidential.
