# AGENTS.md - Personal AI System

This is the **canonical agent-memory file**, read natively by Codex and
opencode. `CLAUDE.md` is a symlink to it, so Claude Code reads the same thing -
edit this file, never a divergent copy. (Wiring: `memory-sync.sh` in the
cross-harness kit.)

Read this before making changes. This is a **local-only personal AI
assistant that will hold real health/financial/personal data**. Security
mistakes here have real consequences for the user, not just this codebase.

Full security architecture, threat model, and residual-risk decisions:
**`docs/security_design.md`**. Read it before touching `src/security/`,
`src/data_vault/`, or anything that handles the encryption key. Don't
re-derive that context by re-reading every file - it's already written down.

## Before making a change

1. If the change touches auth, encryption, the vault, or RAG/ChromaDB: read
   `docs/security_design.md` first. It records *why* things are the way they
   are (e.g. why the DEK lives only in `st.session_state`, why `.chroma` is
   plaintext-on-disk-but-locked-down-and-colocated rather than encrypted).
   Don't "fix" something there without understanding the tradeoff already
   made.
2. Check `src/config.py` before hardcoding a model name, host, or
   context-window number anywhere. Four files used to each hardcode
   `llama3.2:latest` independently and drifted out of sync - that's how the
   app ended up crashing on a missing embedding model. If you need a new
   configurable value, add it there, not inline.
3. Grep for existing usages before adding a new class/helper that looks like
   it might already exist (e.g. this repo once had two different
   `KeyManager` classes in two files - only one was ever imported, the other
   silently rotted).
4. Adding support for a new document provider/category (e.g. a new utility
   or card issuer)? Add one entry to `CATEGORIES` in
   `src/data_extraction/categories.py` - that alone gets you keyword-based
   classification, an Upload-page metadata form, and inclusion in
   structured-record filtering (`ChatEngine.get_structured_records`) and the
   Dashboard/Files-page groupings. Don't hand-roll a separate
   classifier/extractor path for a new category.

## Security checklist for any change touching security/ or data_vault/

- [ ] Does this ever write plaintext personal data (vault content, chat
      history, uploaded file text) to disk outside the encrypted vault? If
      yes, does it need to be, and is the residual risk documented in
      `docs/security_design.md`?
- [ ] Does this read or derive the vault encryption key anywhere other than
      via `AuthManager` (`src/security/auth.py`)? There should be exactly one
      path from "user's password" to "usable DEK." Don't add a second one
      (e.g. don't reintroduce reading a raw key file directly).
- [ ] Does `store_data(..., encrypt=True)` actually have a key loaded, or
      will it now correctly raise (see `EncryptedSQLiteDB.store_data`)
      instead of silently storing plaintext? Don't loosen that guard.
- [ ] If you change `PasswordKeyDerivation.ITERATIONS` or the wrap/unwrap
      format in `auth.py`, existing `~/.personal_ai_auth` files become
      unreadable. That's acceptable pre-1.0 (no migration exists yet) but
      say so explicitly in your summary to the user - don't let them
      silently lose vault access.
- [ ] New file-upload or parser code (`src/data_ingestion/parsers/`): validate
      it can't be pointed at an arbitrary path outside the intended upload
      flow (path traversal) and that a malformed file fails closed (raises),
      not open (returns partial/empty data treated as success).

## After making a change

- Run `python3 -c "import ast; ast.parse(open('<file>').read())"` or just
  import the module - this project has no CI yet, so a syntax error in an
  unexercised code path won't be caught otherwise.
- If you touched anything in the chat/RAG path, run `make check-context`
  (see below) - it's cheap and catches context-window assumption drift
  before it becomes a runtime crash like the one this app shipped with.
- If you touched auth, actually run `make demo` (or the Streamlit app) and
  go through register → lock → login with a wrong password → login with the
  right password. This flow has no automated tests yet; manual verification
  is the only guard.
- Update `docs/security_design.md`'s changelog section if you changed
  anything it documents - don't let it drift out of date the way the root
  `README.md` architecture diagram already had (it described a design that
  was never actually built this way).

## Interface / UI (Streamlit) conventions

The UI is Streamlit (`src/interface/`). Conventions settled on so far:

- **Theme lives in `.streamlit/config.toml`.** Keep `[theme].primaryColor`
  aligned with the dashboard's chart palette (`dashboard.py`'s
  `_SEQUENTIAL_BLUE`, `#2a78d6`) so chrome and charts read as one system. The
  charts assume a light background - the theme is pinned to light on purpose.
- **Never touch `[server].address`** in `config.toml`. The localhost binding is
  a security guardrail (see the comment there and `docs/security_design.md`) -
  removing it exposes the vault login to the LAN.
- **Avoid deep `st.expander` nesting.** Streamlit 1.62 renders nested expanders
  but explicitly discourages them (bad on small screens). Prefer showing one
  thing at a time - a search box + `st.segmented_control` to pick a category,
  say - over dumping a long, deep, scrollable list. File cards are already one
  expander level; don't wrap another expander around them.
- **Settings/config forms: single source of truth.** Prefill inputs from the
  saved vault config and use one Save action. Don't render two headers or two
  Save buttons for one form (a Save button that only returns a dict without
  persisting is a trap - persist in one place).

## Testing the UI without a browser

Streamlit ships a headless test harness - use it instead of "looks fine to me":

- `from streamlit.testing.v1 import AppTest`; `AppTest.from_file("src/interface/main.py")`,
  `.run()`, then assert `not at.exception` and inspect `at.header`, `at.radio`,
  `at.subheader`, `at.session_state`, etc. `at.radio[0].set_value(...).run()`
  drives navigation.
- To exercise pages **past the login gate without touching the real vault**:
  set a throwaway `HOME`, call `AuthManager().register(<pw>)` to mint a DEK,
  seed `at.session_state["vault_key"] = dek`, then run. This never reads or
  writes `~/.personal_ai_vault`.
- `AppTest.from_function` needs real source on disk; use `from_file` (or a temp
  script) - inline lambdas fail with `OSError: could not get source code`.

## Repo automation to expect

- A **PostToolUse auto-format hook** reformats files right after you edit them.
  Don't fight it or re-edit to "fix" whitespace it changed. When you add an
  import, add its usage in the **same** edit so the formatter/linter doesn't
  strip an apparently-unused import between steps.

## Makefile targets

- `make install` - install deps
- `make run` - start the Streamlit app
- `make demo` - run the CLI demo (exercises auth + vault + RAG + chat end to
  end; good smoke test after any change)
- `make check-context` - validate that `src/config.py`'s assumed context
  window isn't larger than what Ollama actually reports for the configured
  model, and optionally that a given file (`FILE=path`) fits the RAG context
  budget. Run this whenever you change models, context-window config, or
  prompt-assembly logic in `chat_engine.py`.
- `make test` - runs `pytest tests/`. Covers `ChatHistory`/`ChatMessage`
  (`tests/test_chat.py`), `ChatEngine` prompt-building and streaming
  (`tests/test_chat_engine.py`, via fake Ollama/RAG doubles - no real Ollama
  needed), and `ChromaStore` metadata handling (`tests/test_chroma_store.py`,
  runs fully offline via ChromaDB's local default embedding function).
  Coverage has since grown well beyond those three files -
  `tests/test_categories.py`, `test_classifier.py`, `test_extractor.py`, and
  `test_query_analysis.py` cover the document-category pipeline;
  `test_data_vault.py`, `test_file_grouping.py`, `test_chat_sessions.py`,
  `test_dashboard_data.py`, and `test_ollama_client.py` cover the vault's
  internal-key handling, Files-page category grouping, multi-session chat
  management, and Dashboard aggregation respectively. Add new core-logic
  tests here rather than one-off scripts at the repo root.

## Token economy - things the coding agent should not do on this repo

These are about *your own behavior as a coding agent working on this
codebase*, separate from the app's own LLM-prompt budgeting described above.

- Don't re-read a file you already have open in context just to "confirm"
  an edit landed - the tool result already tells you whether it succeeded.
- Don't dump entire files into chat/output when a targeted grep or a 10-line
  Read range answers the question. This repo has several 150-500 line
  files (`interface/main.py`, `docs/*.md`) - read the section you need, not
  the whole file, once you know where it is.
- Don't paste full vault/database contents, full ChromaDB dumps, or full
  `ollama show` output into a response for debugging - summarize the
  relevant field(s). Vault contents may be real personal data; never echo
  them into logs, commit messages, or chat output beyond what's needed to
  fix the immediate bug.
- Don't run `ollama pull` speculatively "just in case" - it downloads
  hundreds of MB per model. Check `ollama list` first.
- Don't re-explain the architecture or threat model in your own responses
  once `docs/security_design.md` exists - link to it, quote the one relevant
  paragraph if needed, and spend the output budget on the actual change.
- Prefer one targeted `Edit` over rewriting a whole file with `Write` when
  only a few lines change - smaller diffs are cheaper to generate and
  cheaper for a human/agent to review afterward.
- If you're about to feed a large document (a bill PDF, a health export,
  chat history) into an LLM call - either through this app's own RAG path or
  through your own tool calls while developing - run `make check-context
  FILE=...` first instead of finding out via a truncated or failed response.
