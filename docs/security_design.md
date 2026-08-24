# Security Design

This document describes the actual security architecture of the Personal AI
System as implemented (not the aspirational one in the root README), the
residual risks that are deliberately accepted for now, and a changelog of the
security-focused pass done on 2026-08-23.

## Threat model

This is a **single-user, local-machine** system. It defends against:

- Another user, process, or piece of malware reading your data files directly
  off disk while you are *not* logged into the app (vault at rest).
- Casual/opportunistic access to the machine (e.g. someone picks up an
  unlocked laptop with the app not actively unlocked).

It does **not** currently defend against:

- A compromised or malicious process running *as your own user account* while
  the vault is unlocked (it can read process memory / hook Python).
- Someone with root/admin access to the machine.
- Network exposure - the Streamlit app must stay bound to `localhost` (the
  default). Do not put it behind a reverse proxy or expose the port without
  adding real network auth in front of it; the login gate described below is
  a local single-user gate, not a multi-user auth system.

## Key management (as of 2026-08-23)

**Before this pass:** the vault's AES-256-GCM data-encryption key (DEK) was a
random 32-byte value written straight to `~/.personal_ai_key` in plaintext,
protected only by Unix file permissions (`0600`). The Streamlit app read that
file directly and never asked for a password - there was no login screen at
all, despite an `AuthManager` module existing separately. Anyone/anything
running as your user (or with read access to your home directory) had
unconditional access to every byte in the vault.

**Now:** `src/security/auth.py` (`AuthManager`) owns the DEK's lifecycle:

- `register(password)` generates a random DEK, derives a key-encryption key
  (KEK) from the password via PBKDF2-HMAC-SHA256 (600,000 iterations - the
  2023 OWASP-recommended minimum, up from the previous inconsistent
  100k/210k split between `auth.py` and `encryption.py`), and AEAD-encrypts
  ("wraps") the DEK with the KEK. Only the wrapped DEK, salt, and iteration
  count are persisted, to `~/.personal_ai_auth` (`0600`).
- `authenticate(password)` re-derives the KEK and attempts to AEAD-decrypt
  the wrapped DEK. A wrong password fails the AEAD authentication tag check -
  that failure *is* the password check. There is no separate password hash
  to keep in sync with the wrapping key.
- Failed attempts and lockout state are persisted **in the auth file itself**
  (not just in-process), so a script cannot bypass the 5-attempt lockout by
  spawning a fresh process. Lockout is 5 minutes after 5 consecutive
  failures, and resets on success.
- The Streamlit app (`src/interface/main.py`) now gates all rendering behind
  `_render_login_gate()`: first run asks you to set a password (min 8 chars)
  and creates the vault; subsequent runs require that password before any
  page renders. The unwrapped DEK lives only in `st.session_state` for the
  duration of the session - never written to disk in plaintext. A "Lock
  vault" button in the sidebar clears it.
- `~/.personal_ai_key` (the old raw-key file) is no longer read by the app at
  all. If it exists on your machine from before this change, it is now dead
  weight - safe to delete once you've re-registered.

**Residual risk:** the KEK/DEK unwrap happens in-process and the DEK sits in
memory (and in `st.session_state`, which Streamlit keeps server-side) for the
life of the session. This is standard for a local single-user app and is not
addressed further here.

## Data vault (`src/data_vault/`)

- `EncryptedSQLiteDB.store_data()` used to silently fall back to storing data
  **unencrypted** whenever `encrypt=True` was requested but no key was
  loaded - the `if self._db is None: raise ...` guard in `DataVault` was dead
  code because the DB connection was always created regardless of whether a
  key was supplied. Fixed: `store_data()` now raises `DatabaseError` if
  `encrypt=True` and no encryption key is loaded, instead of silently
  degrading to plaintext. Callers must pass `encrypt=False` explicitly if
  unencrypted storage is genuinely intended (used only for the small,
  non-sensitive `vault_metadata` row).

## RAG / ChromaDB (accepted residual risk)

`ChatEngine.initialize_rag()` copies **decrypted** vault document text into
ChromaDB so it can do local semantic search. This duplicates sensitive
content in plaintext outside the encrypted vault, in `.chroma/`.

Full mitigation (encrypting the vector index itself) is out of scope for now
- it would require either an encrypted filesystem-level wrapper or a
different vector store with at-rest encryption, both bigger changes than fit
this pass. What was done instead:

- The `.chroma` directory moved from the process's current working directory
  (wherever the app happened to be launched from) to
  `<vault_path>/.chroma`, colocated with the encrypted vault.
- That directory is created (and re-chmod'd on every init) with `0700`
  permissions.
- This document + a docstring on `ChromaStore` record the risk explicitly.

**If you add real health/financial data**, be aware the semantic index of
that data exists in plaintext on disk at `~/.personal_ai_vault/.chroma/`.
Back it up only if your backup destination is itself encrypted, and don't
sync it to cloud storage unencrypted.

## Model configuration

Chat/embedding model names and the Ollama host were previously hardcoded to
`llama3.2:latest` / `nomic-embed-text` / `http://localhost:11434` in four
separate files (`ollama_client.py`, `chat_engine.py`, `interface/config.py`,
`interface/main.py`), which is how the app ended up crashing when only
`llama3.2` and `qwen3:8b` were pulled but the code assumed
`nomic-embed-text` was also present.

Now centralized in `src/config.py`, overridable via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PERSONAL_AI_OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `PERSONAL_AI_CHAT_MODEL` | `qwen3:8b` | Chat model |
| `PERSONAL_AI_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `PERSONAL_AI_CONTEXT_WINDOW_TOKENS` | `8192` | Assumed context window for the RAG truncation guard - see below |

`nomic-embed-text` was pulled via `ollama pull nomic-embed-text` as part of
this fix; it's required regardless of which chat model you use, since
embeddings and chat are separate models in Ollama.

## Context-overflow guard

`ChatEngine._fit_context_to_budget()` now truncates RAG context (using a
coarse chars-per-token estimate, `src.config.CHARS_PER_TOKEN_ESTIMATE = 4`)
so that context + query + a 1024-token reserve for the system prompt/reply
never exceeds `DEFAULT_CONTEXT_WINDOW_TOKENS`. This trades perfect accuracy
(no real tokenizer) for zero added dependencies and catches gross overflows.

`make check-context` (`scripts/check_context_overflow.py`) runs the same
estimate offline against Ollama's actually-reported context window for the
configured model, and optionally against a specific file, so both a human
and an AI coding agent can sanity-check before running the full app. See
`AGENTS.md` for when to run it.

## Other bugs fixed in this pass

- `PasswordKeyDerivation.encrypt_with_password()` in
  `src/security/encryption.py` swapped the `(key, salt)` tuple returned by
  `derive_key()` when unpacking it (`salt, key = derive_key(...)`), which
  meant it passed a 16-byte salt where a 32-byte AES key was required -
  every call would raise `ValueError`. This function was never previously
  exercised by any caller (confirmed via repo-wide grep) - `AuthManager` is
  now its first real caller, via the wrap/unwrap flow above.
- Removed a duplicate `KeyManager` class that existed in both
  `security/encryption.py` and `security/key_manager.py`; the package
  `__init__.py` already imported the `key_manager.py` version, so the
  `encryption.py` copy was dead code that could confuse future edits (fixing
  a bug in one copy while the other silently stayed broken).

## Verification performed

- `examples/ai_engine_demo.py` run end-to-end from a clean state: register →
  encrypted vault write → RAG index build (real `nomic-embed-text` calls) →
  chat query via `qwen3:8b` → correct, context-grounded answers.
- Lockout tested directly: 5 wrong passwords locks the account for 5
  minutes; a *correct* password during that window is still rejected until
  the lockout expires.
- Confirmed `~/.personal_ai_vault/.chroma` is created with `0700`
  permissions and `~/.personal_ai_auth` with `0600`.
- `make check-context` tested both in the pass case (default config vs. the
  real `qwen3:8b` window) and the fail case (a 100k-char file, correctly
  flagged as truncated, non-zero exit code).

## Known limitation: no migration path across a vault key change

There is no tool to re-encrypt existing vault entries after the DEK
changes - whether from a genuine password change (`change_password()`
re-wraps the DEK but doesn't touch already-stored ciphertext, which is fine
since the DEK itself is unchanged) or, more sharply, from **any vault
created before the 2026-08-23 auth overhaul**, where `~/.personal_ai_vault`
already contained entries encrypted under the old raw key file
(`~/.personal_ai_key`). Registering a new password now generates a brand
new random DEK unrelated to that old key, so old entries (commonly
`chat_history`, `ollama_config`, or previously uploaded files) fail to
decrypt with `DataVaultError: Decryption failed`.

This is expected, not a bug in the crypto - but it used to crash the app
outright, because `ChatHistory._load_history()`, `_load_ollama_config()`,
and the sidebar "Vault Status" listing called `vault.retrieve_data()`
without catching `DataVaultError`. Fixed: all three now catch it, show a
`st.warning`, and degrade (empty history / default config / "could not
decrypt" label per file) instead of crashing the page. The Files page loop
already had this pattern; it was just inconsistently applied.

**If you hit `Decryption failed` errors after setting a new vault
password**, it means `~/.personal_ai_vault/vault.db` predates your current
password and its old entries are unrecoverable without the old key. There is
no recovery tool - back up the directory if you want to keep it, then
delete `~/.personal_ai_vault` to start clean under the new password.

