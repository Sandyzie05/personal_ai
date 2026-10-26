# Security design

The security architecture of personal_ai as implemented, the risks accepted on purpose, and a dated log of security work.
For the agent-specific rules and their tests see [platform_plan.md](platform_plan.md) §2 (invariants S1-S16).

## At a glance

| Area | Control |
|---|---|
| Data at rest | AES-256-GCM vault; key derived from your password; single key path (`AuthManager`); `store_data(encrypt=True)` fails without a key |
| Network | App bound to localhost; local inference on loopback (oMLX); only fixed public market routes leave the machine (tickers/topics only) |
| Untrusted content | Email/portal/web/news text goes only through a tool-less schema extractor; findings carry a trust flag; rendered output is escaped |
| Agents | Typed per-edge message schemas, PII/canary scan, audit log (field names + hash only); read-only MCP by default; confirmation gate for write tools |
| Memory | Agents can only *propose*; you approve in the UI; untrusted-derived items need extra acknowledgement |
| Scope | Trading, payments, shell commands, diagnosis and secret disclosure are refused before planning |
| Accepted risk | ChromaDB index is plaintext on disk (locked-down, colocated; see below); a malicious process under your own account while unlocked |

## Contents

[Threat model](#threat-model) · [Key management](#key-management-as-of-2026-08-23) · [Data vault](#data-vault-srcdata_vault) ·
[RAG / ChromaDB](#rag--chromadb-accepted-residual-risk) · [Multi-session chat](#multi-session-chat-and-dashboard-2026-08-25) ·
[Multi-agent and MCP](#multi-agent-and-mcp-integration-2026-10-13) · [Forecast integration](#requested-ticker-forecast-integration-2026-10-18) ·
[Agent platform + audit](#agent-platform-coordinator-quarantine-scoped-bus-core-memory-gate-2026-10-25) ·
[Planned ingestion hardening](#planned-bill-ingestion-and-rag-hardening-design-only-2026-10-25) · [Model configuration](#model-configuration) ·
[Known limitation](#known-limitation-no-migration-path-across-a-vault-key-change)

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
older version of this app, check for and manually delete that file.

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
sends the user's chat messages, the tool arguments the model constructs,
and - when the agent has RAG enabled - retrieved vault context, to that
server over the internet; tool results come back the same way. This is an
**explicit, per-agent opt-in**: an agent only gets a server's tools if the
user attached that server on the Agents page, and the agent form shows a
data-egress warning (naming the remote server) whenever RAG is on plus a
remote server is attached. Mitigations already in place: the app remains
localhost-only (the Streamlit server still binds 127.0.0.1); per-agent RAG
category scoping (`rag_scope_categories`) limits which documents can be
retrieved into an agent's context; `rag_enabled: false` removes vault
context from the agent's turns entirely. **Accepted risk**, same class as
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

**Confirmation gate enforced by the runtime, not the prompt.** Sensitive
(state-mutating) tool calls - MCP `readOnlyHint=false` when the server
annotates, else a name-prefix heuristic (`place_`, `create_`, `cancel_`,
`update_`, `delete_`, ...) - pause the agent loop and persist the full
in-flight state to `agent_run_<session_id>` in the vault; the model can
only proceed after the user clicks Allow/Deny/Abort in the UI. The gate
lives in `AgentRuntime` (`src/agents/runtime.py`), so it cannot be bypassed
by prompt injection, and a paused run survives reruns/tab closes/app
restarts (vault-backed, never only `st.session_state`). Every tool call -
including denied ones - is audited in `agent_activity_log` (args digest
capped at 200 chars, outcome, duration), capped at 500 entries.

**Skills are plaintext instruction files.** Agent skills
(`~/.personal_ai/skills/<slug>.md`, overridable via
`PERSONAL_AI_SKILLS_DIR`) are plain markdown on disk - they are
*instructions* (no personal data by construction), the same accepted
tradeoff as the `.chroma` index. They are injected into the agent's system
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

## Agent platform: coordinator, quarantine, scoped bus, Core Memory gate (2026-10-25)

Code: `src/agents/orchestration/`. Tests: `tests/test_orchestration*.py` (+ `make eval`).

- **Untrusted content never reaches a decision-maker.** Email/portal/news text goes only to `quarantine.extract_facts`:
  zero-width/control chars stripped, length-capped, wrapped in `<untrusted>`, sent to a **tool-less** model call that must
  return a strict schema whose strings use allowlist regexes (`SAFE_TEXT`). Injected prose fails validation and is dropped;
  injection-looking input is audited (`injection_flagged`) but inert either way.
- **Taint.** Findings derived from untrusted sources carry `trust="untrusted"`; merge propagates it. Core Memory approval
  of an untrusted-derived item needs a separate acknowledgement.
- **Least-disclosure bus.** Each (sender -> receiver) edge has a typed `extra="forbid"` schema; string fields are scanned for
  PII patterns and seeded canary tokens; violations are blocked and audited (field names + hash only, never payloads).
- **Core Memory.** Agents can only `propose`; `approve` requires a single-use UI-minted approval token and exists only behind the
  Agents -> Core Memory UI. Corrections supersede (history retained).
- **Gmail** is reached via `ReadOnlyMcp` (allowlist `search|get|list|read_*`, sensitive-prefix tools refused).
- **Egress delta:** oMLX is local (127.0.0.1); Ollama disabled by default (`PERSONAL_AI_OLLAMA_ENABLED`). Forecaster public
  providers gained one fixed route (`yahoo-search`, news) with bounded query/size; no private data is sent.
- **Audit 2026-10-25 (independent reviewer, fixed):** unapproved Core Memory was reachable by RAG (keys now internal); free text
  from email/portal could reach summaries/history (closed enums, tight allowlists, templated summaries, markdown escaping);
  coordinator timeouts were not per-step and results could mutate after a timeout (per-future deadlines, immutable results);
  conflicts between `ok` and `uncertain` findings were dropped; bus gaps (dashed cards, obfuscated emails, free-text symbols,
  absurd amounts, no production canaries); Ollama gate bypass by spelling; oMLX host unvalidated (now loopback-only; token never
  via redirect/proxy); vault used from worker threads (snapshots + calling-thread audit). Details and the open items: `docs/platform_plan.md` §6.1-6.2.
- **Residual risks:** the regex PII scan is heuristic (schemas are the primary control); the quarantine model is itself an LLM
  (schema validation, not model obedience, is the guarantee); portal fetching of real sites is not implemented (interface +
  fixtures only); embedding model change requires a Chroma re-index (Chat -> Advanced -> Rebuild RAG Index now truly re-embeds).

## Planned: bill ingestion and RAG hardening (design only, 2026-10-25)

Not implemented. Requirements S-R1..S-R8 in `docs/rag_design.md` §6 (untrusted PDFs, Gmail read-only attachments, browser-agent
constraints, no plaintext copies, ids-only retrieval logs, bounded idempotent ingestion). Any new index structure (BM25/FTS,
manifest, expected-bills ledger) must land in the encrypted vault or be recorded here as plaintext-at-rest before merge.

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
| `PERSONAL_AI_CONTEXT_WINDOW_TOKENS` | `8192` | Context window size: both the RAG/history truncation guard's budget assumption *and* the literal `num_ctx` value passed to Ollama on every `chat()` call (see "Multi-session chat and dashboard" above) - the two can no longer silently disagree |

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

 **Deleting an MCP server removes its OAuth credentials too** (multi-agent
 layer, 2026-10-13): the delete action on the Agents page removes both the
 `mcp_servers` entry and the `mcp_oauth_<server_id>` token record, so no
 orphaned credentials linger for a server that no longer exists. (Related:
 deleting a *chat session* removes its history key but not a paused
 `agent_run_<session_id>` record if one happened to exist at that moment -
 it stays encrypted and inert in the vault, and is overwritten if the same
 session id were ever reused, which it won't be since ids are random.)


**RAG chunking fix (R0, 2026-10-25):** `chroma_store._chunk_text` now packs lines into chunks instead of one chunk per line; collection renamed `personal_ai_vault_v4` so the old per-line index is not reused (rebuild required). No change to the plaintext-Chroma residual risk; the old v3 collection directory remains on disk under `~/.personal_ai_vault/.chroma` until the index is rebuilt/cleared.

**RAG retrieval and grounding (R1-R6, 2026-10-25):** BM25 index (`ai_engine/hybrid.py`) is memory-only, rebuilt from the live Chroma collection (no second on-disk text index, S-R3). Embedding cache lives in the encrypted vault as internal `vault_embcache_*` keys (never indexed/listed). Per-turn diagnostics (`agent_activity_log`, `kind=rag_turn`) hold evidence ids, scores and flags only, never question/chunk text. Chroma `where` uses `$and` + integer period fields. Answers are grounded deterministically (`grounding.py`); empty retrieval returns a templated not-found with no model call. Chroma currently runs in-memory (`.chroma` stays empty), so the plaintext-on-disk residual risk is smaller than documented; if persistence is enabled the old risk applies. Collection `personal_ai_vault_v4` is stamped (`embed_model`, `chunker_version`); mismatch is reported stale, never mixed.

**Bill ingestion + portal agent (R7-R10, 2026-10-25):** S-R1: `files.validate_pdf` gates every downloaded/attached file (magic bytes, 15 MB/60-page caps, rejects JS/Launch/EmbeddedFile/RichMedia incl. `#XX`-obfuscated names and object streams, embedded files, encrypted PDFs); files stored encrypted as `ingest_<sha16>` before parsing, parsed in memory, remote filename never used. S-R3: `ingest_manifest`, `ingest_expectations`, `portal_recipes`, `portal_cred_*` are internal encrypted vault keys. Residual risk: Playwright downloads touch a 0700 temp dir briefly (deleted after read) and its persistent profile (`~/.personal_ai_vault/.portal_profiles`, 0700) holds session cookies in plaintext. S-R4: Gmail attachments via `ReadOnlyMcp` only, code-side sender allowlist, no body text read. S-R5: recipes are data with a closed action set, https + host allowlist, payment/settings deny list; off-allowlist navigation aborts the run; credentials only reach `fill_*` steps, never logs/errors/models; 2FA/CAPTCHA stops for the human; fetch only on explicit button press. S-R7: per-run file/time caps, idempotent hash dedupe, no archive recursion. Evals: hostile files accepted 0/10, credential leaks 0, unblocked off-allowlist redirects 0.

**Agent platform hardening (2026-10-25):** Core Memory approval needs a single-use, session- and item-bound token minted only by the Core Memory button handler (`memory.issue_approval_token`; a test pins callers); forged/replayed/cross-item tokens fail closed (structural within the process, not cryptographic). Schedule and Core Memory read-modify-write take a cross-process `flock` (`~/.personal_ai/*.lock`, 0600); scheduled jobs are claimed atomically. Gmail/brokerage MCP reads are prefetched on the calling thread (S15); only validated tickers survive. Bus dedupes identical retried sends; timed-out workers get a cooperative cancel. Verifier can only add escalations. Optional LLM planner is off by default and gated by `Plan` + `validate_plan`. oMLX client: no invented tool-call ids, correct `json_schema` response format, streamed tool-call deltas accumulated. News failures carry `yahoo-news` source id.
