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
- 💬 Local chat over your vault data using any Ollama chat model, with real
  multi-turn conversation memory, a live context-window usage meter, and
  multiple named/switchable/deletable chat sessions (`src/interface/chat_sessions.py`)
- 📂 File ingestion: PDF, DOCX, CSV, Apple Health XML, plus a category-driven
  document pipeline (electricity, gas, credit card, checking, brokerage,
  mobile/phone, ...) with auto-detected categorization and LLM-based
  structured extraction (`src/data_extraction/`) so questions like "how much
  did I pay T-Mobile last month" can be answered from real extracted numbers
  instead of fuzzy retrieval
- 🗂️ Files page grouped into per-category folders (e.g. "Mobile / Phone"),
  with a re-categorize control for anything misclassified
- 📊 Dashboard page with configurable widgets - document counts, spend by
  category, spend over time, recent uploads - built from the same structured
  extraction data, not a RAG summary
- 🖥️ Streamlit web UI: chat, upload, files, dashboard, and settings pages

## Project structure

```
src/
├── security/        # Auth (password -> vault key), AES-256-GCM encryption
├── data_vault/       # Encrypted SQLite storage
├── ai_engine/        # Ollama client, RAG engine, ChromaDB vector store,
│                       chat history/context budgeting (chat_engine.py)
├── data_ingestion/    # File upload handling + per-format parsers
├── data_extraction/   # Category registry, classifier, LLM structured
│                       extraction (categories.py, classifier.py, extractor.py)
├── interface/         # Streamlit UI: chat, chat_sessions, upload, files
│                       (file_grouping.py), dashboard, settings
└── config.py           # Central model/host/context-window configuration

scripts/               # Standalone tools (setup, context-overflow checker)
docs/                  # Security design, system design notes, roadmap/history
examples/              # CLI usage examples
```

## Requirements

- macOS, Linux, or Windows
- Python 3.10+
- [Ollama](https://ollama.com/download), installed and running (`ollama serve`)

## Setup

Quickest path - one command does everything below (creates `./venv`,
installs dependencies, checks Ollama is installed and running, pulls the
configured chat + embedding models if you don't already have them):

```bash
git clone <repository-url>
cd personal_ai
make setup   # or: python3 scripts/setup.py
```

It's safe to re-run - every step is skipped if already satisfied. Add
`--skip-models` (e.g. `python3 scripts/setup.py --skip-models`) to skip the
`ollama pull` step. If Ollama isn't installed or isn't running, it stops
with a clear message telling you what to do next rather than guessing.

<details>
<summary>Manual setup (equivalent, step by step)</summary>

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

</details>

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
make setup           # One-command bootstrap (see Setup above); safe to re-run
make demo            # CLI walkthrough: register/login, store data, RAG query
make check-context   # Sanity-check model context-window assumptions (see below)
make test            # Run the test suite
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
- `docs/design_2026_08.md` - how the document pipeline, RAG vs. structured
  datastore, chat context management, and dashboard fit together
- `docs/roadmap.md` - original project plan and feature roadmap (historical; not current state)
- `AGENTS.md` - checklist and conventions for making further changes to this repo
- `INTERFACE_README.md` / `INTERFACE_SUMMARY.md` - Streamlit UI notes

## Contributing & Maintenance

This is a personal project, but contributions and suggestions are welcome.
Always prioritize security over features - see `AGENTS.md` before changing
anything under `src/security/` or `src/data_vault/`.

## License

This project is proprietary. All code and data are private and confidential.
