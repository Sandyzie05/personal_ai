# AGENTS.md - Personal AI System

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
- `make test` - runs `test_metadata_fix.py` (the only test file that exists;
  there is no real test suite yet - adding one would be a good next step,
  not an assumption you should make already exists)

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
