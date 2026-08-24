# Changelog

Commit history for `personal_ai`, newest first, with what actually changed
and why - kept for future reference so you don't have to reconstruct intent
from `git log` alone. Update this file whenever you commit.

## a3d32ca - 2026-08-23 - fix: don't crash on vault entries encrypted under a stale key

`ChatHistory._load_history()`, `_load_ollama_config()`, and the sidebar
"Vault Status" file listing called `vault.retrieve_data()` without catching
`DataVaultError`. A single undecryptable record (most commonly: data left
over from before the password/auth overhaul below, encrypted under a
different key than the one currently unlocked) crashed the whole app
instead of degrading gracefully. All three now catch the error, show a
warning, and continue. See `docs/security_design.md` -> "Known limitation:
no migration path across a vault key change" for the full explanation and
recovery steps if you hit this.

- `src/interface/chat.py`, `src/interface/main.py`, `docs/security_design.md`

## c5be28f - 2026-08-23 - chore: update readme

Split the README: it previously mixed the original aspirational plan
(phased roadmap, feature checklist, an architecture diagram with
never-built pieces like LlamaIndex/Touch ID/email import) with actual setup
instructions. Moved all planning/roadmap/status content to the new
`docs/roadmap.md` (marked historical), leaving README as a real "what this
is and how to run it" doc.

- `README.md`, `docs/roadmap.md`

## 1494bb9 - 2026-08-23 - chore: update/fix with cluade

Full security review and remediation pass - the big one. Before this
commit, the Streamlit app had **no login screen at all**: the vault's
AES-256-GCM encryption key was a plaintext file
(`~/.personal_ai_key`, `0600`), readable by anything running as the local
user, and the app crashed on startup because it was hardcoded to use the
`nomic-embed-text` embedding model, which wasn't pulled in Ollama.

Key changes:
- **Auth overhaul** (`src/security/auth.py`): `AuthManager` now owns the
  vault's data-encryption key (DEK) lifecycle. `register(password)`
  generates a random DEK and wraps it with a password-derived key
  (PBKDF2-HMAC-SHA256, 600k iterations - up from an inconsistent
  100k/210k split). `authenticate(password)` unwraps it; a wrong password
  fails the AEAD tag check, which *is* the password check. Failed attempts
  and lockout (5 attempts -> 5 min) are persisted to disk, not just
  in-process.
- **Streamlit login gate** (`src/interface/main.py`): all rendering is now
  gated behind register/login; the unwrapped DEK lives only in
  `st.session_state`, never written to disk in plaintext. Added a "Lock
  vault" button.
- **`DataVault` no longer silently stores plaintext** (`src/data_vault/database.py`):
  previously fell back to unencrypted storage whenever `encrypt=True` was
  requested but no key was loaded; now raises instead.
- **RAG/ChromaDB plaintext index relocated + locked down**
  (`src/ai_engine/chroma_store.py`): moved from a cwd-relative `.chroma/`
  to `<vault_path>/.chroma` with `0700` permissions. Documented as an
  accepted residual risk in `docs/security_design.md` (full encryption of
  the vector index is out of scope for now).
- **Centralized model/host config** (new `src/config.py`): chat/embedding
  model names and the Ollama host were hardcoded independently in four
  files and had drifted out of sync (that's what caused the
  `nomic-embed-text` crash). Now env-var overridable
  (`PERSONAL_AI_CHAT_MODEL`, `PERSONAL_AI_EMBED_MODEL`,
  `PERSONAL_AI_OLLAMA_HOST`), defaulting the chat model to `qwen3:8b`.
- **Context-overflow guard**: `ChatEngine._fit_context_to_budget()`
  truncates RAG context to fit the model's context window (coarse
  chars-per-token estimate). New `make check-context`
  (`scripts/check_context_overflow.py`) validates that assumption against
  what Ollama actually reports for the configured model, and can check a
  specific file against the RAG budget.
- **Streamlit network binding fixed**: added `.streamlit/config.toml`
  forcing `server.address = "localhost"` - Streamlit's own default binds to
  all interfaces, which would have exposed the vault's login page to the
  LAN.
- **Bug fixes**: `PasswordKeyDerivation.encrypt_with_password()` swapped a
  `(key, salt)` tuple unpack and would have crashed on first use (never
  previously exercised by any caller). Removed a duplicate, dead
  `KeyManager` class that existed in both `encryption.py` and
  `key_manager.py`.
- **Dependency cleanup**: removed `pysqlcipher3`, `duckdb`, and
  `llama-index*` from `requirements.txt` - none were actually imported
  anywhere in `src/`; added `pydantic`, `python-dateutil`, `keyring`,
  `numpy`, which were used but missing.
- New `AGENTS.md` (before/after checklist + token-economy guidance for any
  agent working on this repo, including opencode) and
  `docs/security_design.md` (full threat model and as-built security
  architecture).

Verified end-to-end via `examples/ai_engine_demo.py` (register -> encrypted
write -> RAG index build -> chat via `qwen3:8b`), a direct lockout test, and
a real (headless) Streamlit server boot confirming `localhost`-only binding.

- 19 files changed, +1284/-526

## 8a920cd - 2026-08-23 - chore: first commit

Initial import of the working prototype: security modules (encryption, key
management, auth), encrypted SQLite data vault, Ollama-backed AI engine
(chat, embeddings, RAG, ChromaDB), Streamlit interface, data ingestion
parsers (CSV/PDF/DOCX/health XML/financial CSV), and the T-Mobile bill
extractor, plus their accompanying README docs
(`INTERFACE_README.md`, `INTERFACE_SUMMARY.md`, `BILL_EXTRACTION_README.md`,
`AGENTS_IMPLEMENTATION_PLAN.md`).

## 2085839 - 2026-08-23 - chore: add gitignore for secure publication

Added `.gitignore` excluding encryption keys, vault data, auth files,
ChromaDB storage, `.env`, and other local/sensitive artifacts before the
first real commit of source went in.

## 988eb8e - 2026-08-23 - first commit

Empty/placeholder initial commit.
