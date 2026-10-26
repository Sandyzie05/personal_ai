# 🛡️ Secure Personal AI System

A personal AI assistant that answers questions about your own data (notes,
bills, health/financial documents, etc.) using local models via
oMLX (default; [Ollama](https://ollama.com) is supported but disabled unless
`PERSONAL_AI_OLLAMA_ENABLED=true`), with an encrypted primary data vault.

**Local-first, not necessarily offline:** keep private model processing on
approved local endpoints. Public market research requires network access;
configured remote MCP tools can also send arguments externally. Review
`docs/security_design.md` before enabling integrations. The local retrieval
index has a documented plaintext-at-rest limitation.

## Features

- 🔒 Password-gated encrypted vault (AES-256-GCM, key derived from your
  password via PBKDF2 - see `docs/security_design.md`)
- 💬 Local chat over your vault data using any local oMLX chat model, with real
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
- Multi-agent platform: a Coordinator delegates to specialist agents in parallel, merges
  findings deterministically, escalates conflicts, quarantines untrusted email/portal
  content, passes only scoped fields between agents, and changes Core Memory only after
  your explicit approval. See `docs/platform_plan.md` (plan + status), `make eval`.
- Grounded document Q&A: structure-aware chunking, contextual headers, hybrid dense + BM25
  retrieval with RRF, evidence-id citations, templated "not found" abstention (no model call),
  and a deterministic check that every figure in an answer appears in the cited context
  (`docs/rag_design.md`, `make eval-rag`).
- Bill freshness: Upload → Sync panel (hash dedupe, corrected-bill supersede, expected-bills
  ledger with overdue flags, hostile-PDF rejection), read-only Gmail attachment fetcher and a
  portal recipe framework (Playwright, human-in-the-loop 2FA). Real provider recipes and live
  Gmail/portal runs are user-supervised. Agents → Scheduled runs jobs on app open; results are
  Core Memory proposals needing your approval.
- Forecast page: requested-US-stock research, recommendations (research lean), validation
  report, events/news, drilldown and screens, optional parallel local
  specialist reviews, and explicitly saved encrypted forecasts/outcomes.
  Scores are heuristic, not calibrated probabilities or investment advice.

## Project structure

```
src/
├── security/        # Auth (password -> vault key), AES-256-GCM encryption
├── data_vault/       # Encrypted SQLite storage
├── ai_engine/        # oMLX/Ollama clients, RAG engine, ChromaDB vector store,
│                       chat history/context budgeting (chat_engine.py)
├── data_ingestion/    # Upload handling, parsers, PDF validation, ingest pipeline + ledger,
│                       Gmail attachments, portal/ (Playwright recipes)
├── data_extraction/   # Category registry, classifier, LLM structured
│                       extraction (categories.py, classifier.py, extractor.py)
├── agents/            # Agent runtime, MCP, skills; orchestration/ = coordinator, bus,
│                       quarantine, Core Memory gate, workers, policy, scheduler
├── forecaster/        # Public market evidence, research, specialist scenarios,
│                       explicit-session forecast/outcome evaluation
├── interface/         # Streamlit UI: chat, chat_sessions, upload, files
│                       (file_grouping.py), dashboard, forecast, settings
└── config.py           # Central model/host/context-window configuration

evals/                 # make eval / eval-rag / eval-live
skills/                # Built-in agent skills (instructions only)
scripts/               # Standalone tools (setup, context-overflow checker)
docs/                  # Security design, system design notes, roadmap/history
examples/              # CLI usage examples
```

## Agentic capstone direction

The planned unified application combines document-backed personal assistance
with requested-stock research from the companion `stock_predictor` project.
Its target architecture uses a bounded coordinator, separate private/public
data boundaries, parallel forecast specialists, approved encrypted memory
and leakage-aware outcome evaluation. Trading and payments are outside the
capstone scope.

See [docs/architecture.md](docs/architecture.md) for the overview and
[docs/platform_plan.md](docs/platform_plan.md) for what is built, what is
open and the rules for changing it. The docs do not claim that every capstone
capability is implemented.

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

- macOS, Linux, or Windows
- Python 3.10+
- An [oMLX](docs/ai_engine.md) server on `127.0.0.1:8000` with your chat and embedding models loaded
  (Ollama is optional legacy: set `PERSONAL_AI_OLLAMA_ENABLED=true`)

## Setup

Quickest path - one command (creates `./venv`, installs dependencies; the
model-pull/Ollama checks only apply to the legacy Ollama path, so use
`--skip-models` with oMLX):

```bash
git clone <repository-url>
cd personal_ai
make setup   # or: python3 scripts/setup.py
```

It's safe to re-run - every step is skipped if already satisfied. Add
`--skip-models` (e.g. `python3 scripts/setup.py --skip-models`) to skip the
`ollama pull` step.

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

# 4. Start oMLX with your chat + embedding models (see Configuration for env vars)
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
make eval            # Offline agent-platform evals (must print Overall: PASS)
make eval-rag        # Offline retrieval/grounding evals; baseline + deltas in evals/results/rag_latest.md
make eval-live       # Opt-in: injection corpus against the real local model (needs oMLX running)
```

Portal automation (optional): `venv/bin/playwright install chromium-headless-shell`.
After upgrading, run Chat → Advanced → **Rebuild RAG Index** (collection `personal_ai_vault_v4`).

## Configuration

Inference is local via **oMLX** (Ollama is disabled unless `PERSONAL_AI_OLLAMA_ENABLED=true`).
Models and hosts are centralized in `src/config.py` and overridable via environment variables;
the full table is in `docs/platform_plan.md` §4:

```bash
export PERSONAL_AI_OMLX_HOST=http://127.0.0.1:8000   # must stay loopback
export PERSONAL_AI_OMLX_CHAT_MODEL=Qwen3.8-27B-4bit
export PERSONAL_AI_OMLX_EMBED_MODEL=mlx-community/Qwen3-VL-Embedding-2B-8bit   # changing it => rebuild index
export OMLX_API_KEY=...                              # only if your server needs one
export PERSONAL_AI_CONTEXT_WINDOW_TOKENS=8192        # must not exceed your chat model's real window
```

Run `make check-context` after changing models to confirm the configured
context window doesn't exceed what the server actually reports for that model -
this catches silent truncation/overflow before it shows up as a bad answer
or a runtime error.

## Security

The vault is encrypted at rest (AES-256-GCM) with a key derived from your
password; the key is never written to disk in plaintext, and repeated wrong
passwords trigger a persistent lockout. Known residual risks: the local
semantic index (ChromaDB) is plaintext if persisted under
`~/.personal_ai_vault/.chroma` (`0700`; currently it runs in-memory and is
rebuilt each session), and portal automation briefly writes downloads to a
`0700` temp dir and keeps session cookies in a `0700` profile dir. Details in the doc below.

Full threat model, what's implemented vs. not, and why: **`docs/security_design.md`**.

## Documentation

Index with reading order: [`docs/README.md`](docs/README.md).

- [`docs/architecture.md`](docs/architecture.md) - system overview (start here)
- [`docs/platform_plan.md`](docs/platform_plan.md) - rules, current status, backlog; also the brief for a coding agent
- [`docs/rag_design.md`](docs/rag_design.md) - how retrieval works today, why answers go wrong, the fix plan, bill ingestion
- [`docs/ai_engine.md`](docs/ai_engine.md) - oMLX, embeddings, re-indexing
- [`docs/forecasting_system.md`](docs/forecasting_system.md) - stock research and outcome tracking
- [`docs/multi_agent_design.md`](docs/multi_agent_design.md) - agent loop, MCP, skills (runtime reference)
- [`docs/security_design.md`](docs/security_design.md) - security architecture, threat model, residual risks, audit log
- [`docs/user_testing_guide.md`](docs/user_testing_guide.md) - hands-on test plan for RAG, agents and predictions
- `AGENTS.md` - checklist and conventions for making changes to this repo
- `CHANGELOG.md` - history

## Contributing & Maintenance

This is a personal project, but contributions and suggestions are welcome.
Always prioritize security over features - see `AGENTS.md` before changing
anything under `src/security/` or `src/data_vault/`.

## License

This project is proprietary. All code and data are private and confidential.
