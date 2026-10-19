# Security Design

This document describes the actual security architecture of the Personal AI
System as implemented (not the aspirational one in the root README), the
residual risks that are deliberately accepted for now, and a changelog of the
security-focused pass done on 2026-08-23, with subsequent changes recorded
in dated sections below. Historical verification is not a current release
certification.

## Threat model

This is a **single-user, local-machine** system. It defends against:

- Direct reads of encrypted vault payloads without the password/DEK.
  This protection does not cover plaintext Chroma/skill copies, metadata,
  old staging files or a compromised local inference process.
- Casual/opportunistic access to the machine (e.g. someone picks up an
  unlocked laptop with the app not actively unlocked).

It does **not** currently defend against:

- A compromised or malicious process running *as your own user account* while
  the vault is unlocked (it can read process memory / hook Python).
- Same-user/admin access to accepted plaintext artifacts, including while
  the application's login gate is locked. App locking is not OS isolation.
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
  page renders. The unwrapped DEK is held in server-side session state and
  DEK-bearing vault/client objects during the session, never intentionally
  persisted in plaintext by the app. A "Lock vault" button removes the
  session key and releases session-owned history, runtime and vault/client
  references. Python memory zeroization is not guaranteed.
- `~/.personal_ai_key` (the old raw-key file) is no longer read by the app at
  all. Preserve it if an older vault may still need recovery: registering a
  new password does not migrate ciphertext encrypted under the old key.
  Never automatically delete old key material.

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

**Document-category pipeline (2026-08-24):** uploaded documents can now
carry a `category` (electricity, gas, credit_card, checking, brokerage,
...) and, when LLM-based structured extraction succeeds, an `extraction`
field (provider, statement period, total, line items) - see
`src/data_extraction/`. Both live inside the same encrypted vault record as
the rest of the document (`src/data_ingestion/handlers.py`,
`ChatEngine.get_structured_records()`), so this doesn't create a new
plaintext-at-rest location. The one addition to the existing residual risk
above: each RAG chunk's `category` string (e.g. `"electricity"`) is now
also written into the plaintext `.chroma` metadata, to let retrieval scope
queries to a category via ChromaDB's `where` filter. This replaces an
earlier, disconnected prototype (`bill_extractor.py`/`bill_storage.py`,
deleted) that stored extracted bill data - account numbers included - in a
**fully unencrypted** SQLite DB at `~/.personal_ai/bills.db`, outside the
vault entirely and never actually wired into the chat flow. If you ran an
older version, treat that file as sensitive plaintext. Review retention and
an encrypted recovery backup before any explicit deletion.

## Multi-session chat and dashboard (2026-08-25)

**Chat history is no longer a single fixed vault key.** Previously all chat
history lived under one `chat_history` key, shared across the whole vault -
there was no concept of separate conversations, only one growing (or
destructively cleared) history. It's now N per-session keys
(`chat_history_<session_id>`, see `src/interface/chat_sessions.py`), tracked
by an index record at `chat_sessions_index`. All of it - the index and every
session's messages - still lives inside the same encrypted vault; this does
not introduce a new plaintext-at-rest location. The first time the chat page
loads after this upgrade, any pre-existing single-conversation history under
the legacy `chat_history` key is auto-migrated (in place, without copying
the messages) into a session titled "Previous Chat"
(`chat_sessions.migrate_legacy_history`); `is_internal_vault_key()` in
`src/data_vault/vault.py` was updated to recognize both `chat_history` and
the `chat_history_<session_id>` prefix as internal (non-document) keys, and
the new `chat_sessions_index` and `dashboard_config` keys are added to
`INTERNAL_VAULT_KEYS` for the same reason.

**Conversation history is now actually sent to Ollama.** Every chat turn was
previously stateless from the model's point of view - only freshly-retrieved
RAG/structured context was ever sent, never prior turns. `ChatEngine`
now threads a budgeted, oldest-first-dropped history
(`_fit_history_to_budget`) into every prompt path
(`query_vault`/`query_vault_stream`/`_build_prompt`). This doesn't change
the trust boundary described above under "RAG / ChromaDB" - Ollama already
received RAG-retrieved vault content on every turn - but it does mean more
of the user's own prior conversation now leaves the vault and is sent, in
plaintext, to the local Ollama process's request payload on each subsequent
turn (still local-only; Ollama is not a network service outside this
machine per the threat model above).

**`num_ctx` is now explicitly passed to Ollama.** `OllamaClient` gained a
`context_window_tokens` constructor parameter (sourced from
`PERSONAL_AI_CONTEXT_WINDOW_TOKENS`, same as before) that is now passed as
`num_ctx` on every `client.chat(...)` call. Previously
`DEFAULT_CONTEXT_WINDOW_TOKENS` was only ever used as this app's own
truncation-guard estimate and had no effect on what Ollama itself was
actually configured to use as its context window - the two values could
silently disagree (e.g. the app assuming a smaller window than Ollama would
actually accept, or vice versa, either wasting headroom or overflowing).
`ChatEngine`'s own budget math (`_total_budget_chars`, `estimate_usage`) now
reads `self.ollama_client.context_window_tokens` directly rather than the
module-level default, so it can't drift from what's really sent to Ollama.

 **Dashboard widget preferences are a new internal vault key.** The Dashboard
 page (`src/interface/dashboard.py`) persists which widgets a user has
 enabled to `dashboard_config` - just `{"enabled_widgets": [...]}`, a list of
 widget-registry keys. No sensitive data, but noted here for completeness
 since it's a new entry in `INTERNAL_VAULT_KEYS`.

## Multi-agent and MCP integration (2026-10-13)

The app gained a multi-agent layer (`src/agents/`, UI page "Agents"; full
design in `docs/multi_agent_design.md`). Six security-relevant deltas:

**New network egress: remote MCP servers.** The previous trust boundary was
"nothing leaves the machine except calls to the local Ollama process". An
agent attached to a *remote* MCP server (e.g. Robinhood's trading endpoint)
sends the selected tool name and model-constructed arguments to that server
over the internet; results return to the runtime. It does not automatically
send the entire model message list. Arguments can nevertheless reproduce
private user text, history or RAG context. This is an
**explicit, per-agent opt-in**: an agent only gets a server's tools if the
user attached that server on the Agents page, and the agent form shows a
data-egress warning (naming the remote server) whenever RAG is on plus a
remote server is attached. Mitigations already in place: the app remains
localhost-only (the Streamlit server still binds 127.0.0.1); per-agent RAG
category scoping (`rag_scope_categories`) limits which documents can be
retrieved into an agent's context; `rag_enabled: false` removes vault
retrieved document context from the agent's turns. This does not remove
private user messages or prior history. **Accepted risk**, same class as
the `.chroma` plaintext index: the user knowingly chose this destination.

**OAuth tokens are encrypted vault records, not a plaintext sidecar.**
Remote MCP servers using OAuth 2.1 (authorization-code + PKCE) get their
access/refresh tokens and dynamically-registered client info stored under
`mcp_oauth_<server_id>` inside the encrypted vault by
`VaultTokenStorage` (`src/agents/mcp.py`) - the `mcp` SDK's `TokenStorage`
protocol, vault-backed. This contrasts with opencode, which caches MCP
tokens in plaintext `~/.local/share/opencode/mcp-auth.json`; this app does
not read or reuse those tokens and creates its own client registration.
Token refresh is transparent (SDK); a failed refresh surfaces as
`needs_auth` and the UI offers "Connect & Authorize" again. Deleting an MCP
server on the Agents page also deletes its `mcp_oauth_<id>` key.

**Configurable confirmation gate enforced by the runtime, not the prompt.** Sensitive
(state-mutating) tool calls - MCP `readOnlyHint=false` when the server
annotates, else a name-prefix heuristic (`place_`, `create_`, `cancel_`,
`update_`, `delete_`, ...) - pause the agent loop and persist the full
in-flight state to `agent_run_<session_id>` in the vault; the model can
only proceed after the user clicks Allow/Deny/Abort in the UI when the
agent's confirmation setting is enabled. The gate lives in `AgentRuntime`
(`src/agents/runtime.py`); prompts cannot grant approval to a paused call.
However, configurable confirmation, server annotations and name heuristics
are not a universal authorization policy or prompt-injection immunity.
A paused run survives reruns/tab closes/app restarts (vault-backed).
Executed and denied tool activity is recorded in `agent_activity_log`,
capped at 500 entries; unknown tool requests are not necessarily recorded
there. It is not a complete audit of every provider/model/tool attempt.

**Skills are plaintext instruction files.** Agent skills
(`~/.personal_ai/skills/<slug>.md`, overridable via
`PERSONAL_AI_SKILLS_DIR`) are plain markdown on disk - they are
*instructions*. Their contents are user-controlled and may include private
data; "no personal data" is a usage requirement, not an enforced property.
This is an accepted plaintext-storage tradeoff. They enter the agent's system
prompt at run time.

**New internal vault keys.** `agents_index`, `mcp_servers`, and
`agent_activity_log` were added to `INTERNAL_VAULT_KEYS`, and
`is_internal_vault_key()` now also recognizes the `agent_run_` and
`mcp_oauth_` prefixes - so OAuth tokens and paused-run state never appear
in the Files page, the sidebar document count, or the RAG index.

**Stdio MCP servers are user-typed local commands.** A "stdio" server entry
spawns the exact command/args the user typed on the Agents page as a child
process and talks MCP over its stdio. This is the same trust level as any
CLI the user runs locally; nothing is enabled by default.

## Requested-ticker Forecast integration (2026-10-18)

The authenticated Streamlit app now has a Forecast page for explicit public
US-ticker research at 5/21 trading-session horizons. Clicking **Research
ticker** requests public provider data; **Run local specialists** is a separate
action using the existing inference factory and saved settings. The
specialist endpoint must name a loopback IP or `localhost`;
remote saved inference endpoints are rejected by this page. Forecast-only
factory options disable redirects and environment proxies in both existing
inference clients (supported Ollama SDK options; a no-redirect urllib opener for
oMLX). Finite connect/read/write/pool timeouts for Ollama, or socket-operation
timeouts for oMLX, are bounded by the remaining specialist budget. These are
**inactivity timeouts, not a hard total request deadline**: genuinely stalled
I/O terminates, but a continuously active/trickling server can outlive the run.
Specialist deadlines stop waiting but cannot forcibly cancel that call.
Client behavior outside this opt-in Forecast path is unchanged.
The research request carries only the validated canonical ticker/horizons and
an optional caller-attested public exchange-session sequence. That sequence
stays local; it is not sent to providers. Current-day candles are eligible only
when the matching supplied session's close is at/before retrieval and cutoff,
including early closes. Without it current-day candles are conservatively
excluded and research cannot be prepared for outcome tracking. Specialist
packets contain public evidence only: no uploaded documents, vault/chat
content, holdings, account data, or agent tools are passed to the forecaster.
Heuristic scores and model scenarios are not calibrated probabilities.

**Persistence requires specific UI consent.** Research/scenarios remain
session-local until the user supplies and confirms an official complete
exchange-session sequence **before Research ticker**, prepares an immutable
forecast from that same captured sequence, reviews its exact
record, and clicks **Save forecast**. Records include forecast/prediction,
snapshot and scorer/model identities, origin/as-of, exchange, session target,
horizon, provider provenance and the asserted session sequence. The payload
digest binds approval to the displayed content; changed payloads are rejected.
`DataVault.store_immutable_data()` uses an atomic insert-once check: repeat
saves are idempotent, while different content under the same ID is rejected.
Forecast and outcome records use `forecast_snapshot_` / `forecast_outcome_`
internal vault prefixes, excluded from document listings/counts and RAG.
They are encrypted with the existing AuthManager-provided DEK; there is no
new key path or plaintext persistence location.

**Outcome tracking is explicit and limited.** There is no automatic exchange
calendar verification/fetch, scheduled evaluator, background report, trading,
or general task-memory coordinator in this page. The user must supply a
complete official session sequence (including holidays correctly) and explicit
source-attributed session closing prices plus an aware evaluation timestamp.
Structural/chronological validation cannot prove that the supplied sequence
matches an official calendar. Missing session prices never use a latest quote
as a substitute. Evaluation/status is displayed before a separate **Save
outcome** action; evaluating or opening saved records does not write forecasts
or outcomes. Saved outcomes retain supplied price provenance.

This page's public-only/no-trading boundary does **not** change existing generic
MCP agent capabilities or revoke user-configured trading tools. A broader
capstone-specific runtime policy remains proposed, not implemented here.

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
| `PERSONAL_AI_CHAT_MODEL` | `llama3.2:latest` | Ollama chat model |
| `PERSONAL_AI_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `PERSONAL_AI_CONTEXT_WINDOW_TOKENS` | `8192` | Context window size: both the RAG/history truncation guard's budget assumption *and* the literal `num_ctx` value passed to Ollama on every `chat()` call (see "Multi-session chat and dashboard" above) - the two can no longer silently disagree |

Embedding and chat backends are independently configured. Chat defaults to
Ollama; `PERSONAL_AI_EMBED_ENGINE` defaults to `omlx`, using the oMLX model
from `src/config.py`. `nomic-embed-text` is the Ollama embedding default,
not a universal requirement for oMLX. Endpoint configuration is not a
general loopback-only enforcement policy: keep private inference local.

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

## Historical verification performed (2026-08-23 pass)

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
 choose a separate empty vault location if starting over. Preserve the old
 directory and key material for possible recovery; deletion is destructive
 and must never be an automatic error-recovery step.

 **Deleting an MCP server removes its OAuth credentials too** (multi-agent
 layer, 2026-10-13): the delete action on the Agents page removes both the
 `mcp_servers` entry and the `mcp_oauth_<server_id>` token record, so no
 orphaned credentials linger for a server that no longer exists. (Related:
 deleting a *chat session* removes its history key but not a paused
 `agent_run_<session_id>` record if one happened to exist at that moment -
 it stays encrypted and inert in the vault, and is overwritten if the same
 session id were ever reused, which it won't be since ids are random.)

## Foundation assessment and remaining boundaries (2026-10-19)

The design review is source-level evidence, not proof that the application
is leak-free or production-ready. Current known work is tracked in
[development_planning.md](development_planning.md).

- **Upload staging:** UI preparation and confirmation now parse uploaded
  bytes in memory and do not retain plaintext temporary paths across reruns.
  Original bytes are stored only through the encrypted vault flow.
  Parser failures fail closed; synthetic regressions cover malformed input,
  hostile filenames and error paths. Bytes/previews still exist in
  Python/Streamlit memory; swap, core dumps and memory zeroization are not
  covered. Old staging files from previous releases are not automatically
  removed; do not treat this change as retroactive cleanup.
- **Lock lifecycle:** teardown clears the session DEK, cached engines/runtime,
  history, pending uploads and session-local private forecast state, and
  releases vault connections. Cleanup errors are surfaced after state
  invalidation. This is reference cleanup, not Python memory zeroization or
  revocation of a DEK already copied by another process/session.
- **Auth durability:** same-directory `0600` staging, file fsync, atomic
  replacement and directory fsync replace truncating writes. A persistent
  POSIX sidecar flock serializes cooperating managers/processes; each
  transaction reads fresh state. Do not remove the lock file while in use.
  Windows authentication fails closed; supported targets are macOS/Linux.
  A directory-sync error is reported after replacement and cannot undo the
  already-committed update. Wrapping format, PBKDF2 parameters and DEKs stay
  compatible; existing registration cannot be silently overwritten.
- **Diagnostics:** routine retrieval logs now contain counts/scores, not
  query/provider/source text. Agent audit arguments use a canonical SHA256
  digest rather than a truncated JSON preview. Synthetic canary regressions
  cover logs and ingestion error messages. Logs/backups are not
  retroactively sanitized, and hashes are not general anonymization.
- **Test data:** the live oMLX diagnostic's credential-shaped assignment and
  credential-prefix printing were removed without validating the value.
  Synthetic mocked requests replace service calls. Historical commits may
  still contain that old value; an owner must revoke/rotate it if it was
  ever valid. No history rewrite or remote credential mutation is performed.
- **Offline regressions:** tests isolate HOME and disable dotenv before
  collection, isolate per-test state, and deny socket/DNS access by default.
  The synthetic OAuth callback test explicitly permits loopback only.
  This is regression isolation, not an OS sandbox or egress security control
  for the running application.
- **Retrieval lifecycle:** complete rebuilds replace indexed source content
  in the active embedding-compatible collection, including an empty vault.
  Collections are namespaced by actual embedding backend, host and model so
  a dimension/model change does not corrupt an incompatible collection.
  Stable chunk identity and per-source updates prevent duplicate accumulation
  and repeated full-vault embedding for batch uploads.
  Delete/reclassification refresh affected source chunks before success is shown.
  On failure the current engine is disabled rather than reporting a
  successful rebuild with stale content. This does not promise transactional
  isolation across multiple independently running app processes or secure
  erasure of historical index copies, old collections and backups. Prior
  embedding collections are preserved, not silently erased.
- **Legacy helpers:** standalone encryption/key-manager APIs still include
  raw-key persistence behavior. They are not the application's supported
  AuthManager path and must not appear in app setup examples.
- **Deployment and supply chain:** localhost binding and encryption are not
  a production-network security posture, dependency audit or external
  endpoint guarantee. No new compliance or absence-of-vulnerability claim
  follows from this checkpoint.
