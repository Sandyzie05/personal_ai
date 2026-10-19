# 🛡️ Secure Personal AI System

A personal AI assistant that answers questions about your own data (notes,
bills, health/financial documents, etc.) using local models via
[Ollama](https://ollama.com) or oMLX, with an encrypted primary data vault.

**Local-first, not necessarily offline:** keep private model processing on
approved local endpoints. Public market research requires network access;
configured remote MCP tools can also send arguments externally. Review
`docs/security_design.md` before enabling integrations. The local retrieval
index has a documented plaintext-at-rest limitation.

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
  category and spend over time - built from the same structured
  extraction data, not a RAG summary
- 🖥️ Streamlit web UI: chat, upload, files, dashboard, and settings pages
- Forecast page: requested-US-stock research, optional parallel local
  specialist reviews, and explicitly saved encrypted forecasts/outcomes.
  Scores are heuristic, not calibrated probabilities or investment advice.

UI uploads are parsed in memory before encrypted storage. DOCX extraction
covers paragraphs/tables; XML extraction recognizes supported health-record
types, not arbitrary XML. Scanned documents have no built-in OCR.

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
├── agents/            # Configurable agents, MCP, encrypted runtime state
├── forecaster/        # Public market evidence, research, specialist scenarios,
│                       explicit-session forecast/outcome evaluation
├── interface/         # Streamlit UI: chat, chat_sessions, upload, files
│                       (file_grouping.py), dashboard, forecast, settings
└── config.py           # Central model/host/context-window configuration

scripts/               # Standalone tools (setup, context-overflow checker)
docs/                  # Security design, system design notes, roadmap/history
examples/              # CLI usage examples
```

## Agentic capstone direction

The planned unified application combines document-backed personal assistance
with the requested-stock research already implemented under `src/forecaster/`.
Its target architecture uses a bounded coordinator, separate private/public
data boundaries, parallel forecast specialists, approved encrypted memory
and leakage-aware outcome evaluation. Trading and payments are outside the
capstone scope.

See [the high-level system design](docs/agentic_system_design.md) for existing
foundations, proposed controls, migration stages and acceptance targets.
The design does not claim that every capstone capability is implemented.

### Forecast workflow

Unlock the existing vault, open **Forecast**, enter a **Public US ticker**
and select **Trading-session horizons** (5 or 21). For trackable forecasts,
provide and attest a complete official exchange-session sequence before
**Research ticker**; the run captures that schedule with its evidence.
Without it, research is explicitly unscheduled. Review provider coverage,
evidence dates and visible failures. **Run local specialists** is a separate
optional action using configured local inference; it receives public
evidence, not your vault documents or chat.

Tracking reuses the captured session sequence. **Prepare forecast** previews the exact snapshot;
**Save forecast** writes it encrypted. Outcome evaluation requires explicit
origin/target closing prices for the symbol and benchmark plus a
timezone-aware evaluation timestamp. **Evaluate outcome** displays the
result; **Save outcome** is a separate approval.

Calendar completeness is caller-attested, not independently verified.
Evaluation is manual: there is no scheduled job, latest-quote substitution,
live-news ingestion or leakage-safe historical backtest. Current SEC/FRED
adapters cannot reconstruct historical data vintages.

Optional provider configuration lives in `src/config.py`, including
`PERSONAL_AI_SEC_USER_AGENT` (SEC contact/identification header),
`PERSONAL_AI_FRED_API_KEY`, and `PERSONAL_AI_ALPHA_VANTAGE_API_KEY`.
Without those settings, coverage is reduced explicitly. Keep credentials
out of source control; do not enter them into forecast prompts.

## Requirements

- macOS or Linux; Windows requires adapting Unix-style setup/Makefile paths
- Python 3.10+
- Streamlit 1.62+ (the UI and headless regressions use current APIs)
- [Ollama](https://ollama.com/download), installed and running (`ollama serve`)

## Setup

Ollama-only setup - the command below creates `./venv`,
installs dependencies, checks Ollama is installed and running, pulls the
configured Ollama chat + embedding models if missing. Explicitly select
Ollama embeddings so this path does not also require an oMLX server:

```bash
git clone <repository-url>
cd personal_ai
export PERSONAL_AI_EMBED_ENGINE=ollama
make setup   # or: python3 scripts/setup.py
```

Re-running reuses the environment but still installs dependencies. Add
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

# 4. For an Ollama-only setup, explicitly select Ollama embeddings
export PERSONAL_AI_EMBED_ENGINE=ollama
ollama pull llama3.2:latest
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
make test            # Isolated offline regressions; one synthetic loopback callback
```

## Configuration

Model names, inference/embedding backends and hosts are centralized in `src/config.py` and
overridable via environment variables instead of editing code:

```bash
export PERSONAL_AI_OLLAMA_HOST=http://localhost:11434
export PERSONAL_AI_CHAT_MODEL=llama3.2:latest
export PERSONAL_AI_EMBED_ENGINE=ollama
export PERSONAL_AI_EMBED_MODEL=nomic-embed-text
export PERSONAL_AI_CONTEXT_WINDOW_TOKENS=8192   # must not exceed your chat model's real window
```

Chat defaults to Ollama; embeddings default to oMLX. The Ollama-only
example overrides that embedding default. Settings can select either
backend independently; an oMLX setup also needs its configured server and
models. Keep private inference endpoints local and trusted: binding the
Streamlit UI to localhost does not constrain outbound model requests.

Run `make check-context` after changing models to confirm the configured
context window doesn't exceed what Ollama actually reports for that model -
this catches silent truncation/overflow before it shows up as a bad answer
or a runtime error.

## Security

The vault is encrypted at rest (AES-256-GCM) with a random data-encryption
key wrapped by a password-derived key. The data-encryption key is not written
to disk in plaintext by the app, and repeated wrong passwords trigger a
persistent lockout. A significant residual risk: the
local semantic search index (ChromaDB) stores document text in plaintext
under `~/.personal_ai_vault/.chroma` (locked to `0700` permissions) to
support fast local search - it is not yet encrypted at rest. Plaintext
skill files, in-memory upload previews, configured remote tools and inference endpoints
have additional privacy boundaries described in the security design.

Full threat model, what's implemented vs. not, and why: **`docs/security_design.md`**.

## Documentation

- `docs/security_design.md` - security architecture, threat model, residual risks
- `docs/agentic_system_design.md` - unified assistant/forecaster capstone design,
  privacy boundaries, migration scope and evaluation targets
- `docs/multi_agent_design.md` - existing configurable agent/MCP architecture;
  broader than the capstone's research-only scope
- `docs/system_architecture.md` - implemented components and trust boundaries
- `docs/ai_engine.md` - inference, embeddings and document retrieval APIs
- `docs/forecasting_system.md` - public research, explicit saves and evaluation limits
- `docs/development_planning.md` - current-state matrix, ordered build steps
  and measurable acceptance gates
- `AGENTS.md` - checklist and conventions for making further changes to this repo

## Contributing & Maintenance

This is a personal project, but contributions and suggestions are welcome.
Always prioritize security over features - see `AGENTS.md` before changing
anything under `src/security/` or `src/data_vault/`.

## License

This project is proprietary. All code and data are private and confidential.
