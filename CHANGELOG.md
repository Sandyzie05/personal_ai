# Changelog

 Commit history for `personal_ai`, newest first, with what actually changed
 and why - kept for future reference so you don't have to reconstruct intent
 from `git log` alone. Update this file whenever you commit.

## (pending) - 2026-08-30 - feat: multi-account disambiguation for credit card/checking uploads + smarter upload form

Ahead of importing multiple credit cards and two checking accounts: the
existing `category` (credit_card/checking/etc) alone can't tell two accounts
of the same type apart, and classification leaned on keyword phrases real
statements don't always spell out literally.

- `schemas.py` / `extractor.py`: `ExtractedDocument` gains
  `account_identifier` - the LLM now also extracts a last-4-digits/"ending in
  1234" identifier straight from the statement when printed, instead of
  relying on the user to type an account nickname on every single upload.
- `categories.py`: added issuer/bank-agnostic keywords to `credit_card`
  ("new balance", "payment due date", "available credit") and `checking`
  ("account summary", "opening/closing balance", "deposits and other
  credits") - real statements often never say the literal words "credit
  card statement" or "checking account".
- `query_analysis.py`: new `detect_provider_from_query(query,
  known_providers)` - resolves which of the user's *own* accounts (sourced
  from their actual uploaded documents, not a static brand list) a question
  names, with the same conservative "exactly one match or None" bar as
  `detect_category_from_query`.
- `chat_engine.py`: `get_structured_records` takes an optional `provider`
  filter; new `_known_providers_for_category` (deliberately independent of
  whether extraction succeeded - a provider is known from upload-time
  metadata regardless). `_build_prompt` now resolves category *and*
  provider, so "what did I spend on my Chase card" with two cards on file
  scopes both the structured-record lookup and the RAG `where` filter to
  Chase only, instead of mixing both cards' numbers. `_rag_metadata_for`
  attaches `provider` to RAG chunks (also fixes an existing gap: chunk
  citations could never show the institution before, `_source_label` was
  looking for a `provider` key that was never set). `_record_label` now
  includes the account identifier so "Chase (...4412)" and "Chase
  (...9981)" render as distinct sources.
- `upload.py`: structured extraction now runs once at file-preview time
  (before the user confirms) instead of only after "Upload All", so its
  results (provider, account identifier, period) pre-fill the confirmation
  form's fields - cached and reused at actual upload time unless the user
  changes the category, to avoid a second LLM call in the common case.
- Added regression tests: `tests/test_query_analysis.py` (provider
  detection), `tests/test_chat_engine.py` (provider-scoped structured
  records and RAG `where`), `tests/test_extractor.py` (account_identifier
  parsing), `tests/test_classifier.py` (checking/credit_card classification
  without the literal phrase), and new `tests/test_upload.py` (extraction
  cache reuse/invalidation).

Deliberately not done: no UI change to *require* an account label, and no
duplicate-statement detection (e.g. warning on re-uploading the same
period/account) - flagging as possible future work rather than adding it
unasked.

## (pending) - 2026-08-30 - fix: mobile-category query routing and explicit month/year date parsing

Reported bug: "summarize the tmobile bill for the month of August 2026"
returned the wrong bill (February 2026) with the model visibly confusing
itself trying to reconcile the mismatch. Root cause was in
`query_analysis.py`, unrelated to the RAG-quality PR merged just before it:

- `detect_category_from_query` had no `CATEGORY_QUERY_ALIASES` entry for the
  `mobile` category at all - its only fallback alias was the full category
  label string (`"📱 Mobile / Phone"`), which never appears verbatim in a
  real question. Every mobile-bill query (T-Mobile, Verizon, AT&T, "phone
  bill") silently fell through to fully unscoped, whole-vault semantic RAG
  instead of category-scoped/structured retrieval - confirmed by calling
  `detect_category_from_query` directly, which returned `None` even for
  `"t-mobile"`. Added aliases: t-mobile, tmobile, verizon, at&t, wireless
  bill, phone bill, mobile bill, cell phone.
- `parse_relative_date_range` only recognized "last N months"/"this
  month"/"last month" - an explicit `"<Month name> <YYYY>"` (e.g. "August
  2026") always returned `None`, so even a correctly-detected category
  pulled *every* record in that category with no date filter. Added a
  `<Month name> <YYYY>` pattern that resolves to that calendar month's
  start/end dates.
- Together these mean a mobile-bill-by-month question now routes through
  `get_structured_records(category="mobile", start_date=..., end_date=...)`
  (which already did real period_start/period_end filtering) instead of
  guessing from raw semantic similarity, which doesn't encode exact
  dates/months well.
- Added regression tests in `tests/test_query_analysis.py` for both gaps.

## (pending) - 2026-08-30 - fix: RAG retrieval quality (embedding prefixes, chunk overlap, per-doc cap, extraction truncation)

Before importing more vendor/carrier bills, audited the actual RAG stack
(code, not assumptions) for quality gaps that would compound as more
documents get indexed:

- `chroma_store.py`: embed calls now prefix text with `nomic-embed-text`'s
  required task instructions (`search_query: ` / `search_document: `) - the
  model card requires these for queries and documents to land in a
  comparable embedding space; the repo had neither. Only the text sent for
  embedding is prefixed, not the stored/returned chunk content.
- `chroma_store.py` / `config.py`: `max_per_document` default raised 1 → 3
  (`DEFAULT_MAX_PER_DOCUMENT`) - the old default meant a multi-page bill
  could only ever contribute its single best-scoring chunk, silently
  dropping a lower-ranked chunk that held the actual line item.
- `chroma_store._chunk_text`: carries up to `DEFAULT_CHUNK_OVERLAP_CHARS`
  (200) from the tail of one chunk into the next when a long paragraph
  needs word-splitting, so a fact split at the cut point still appears
  whole in at least one chunk.
- `extractor.py` / `config.py`: structured-extraction prompt's document-text
  truncation raised 6000 → `DEFAULT_EXTRACTION_TEXT_CHARS` (20000 chars) -
  the old cap silently dropped trailing line items from longer multi-page
  statements (e.g. brokerage transaction history) with no error.
- `chroma_store.py`: default Chroma collection name bumped
  `personal_ai_vault` → `personal_ai_vault_v2`. Adding the Nomic prefixes
  changes what every stored embedding means, so old and new vectors can't
  share one HNSW index without corrupting similarity scores. The new name
  forces a clean, automatic re-embed on next `initialize_rag()` (the vault
  is the source of truth; Chroma is a rebuildable derived index) - the old
  collection is simply abandoned on disk, safe to delete manually.
- `config.py`: corrected a stale comment claiming nomic-embed-text has an
  8192-token context - `ollama show nomic-embed-text` reports the model's
  real trained context is 2048 tokens (8192 was Ollama's `num_ctx`
  parameter, not the model's actual window). No functional change - the
  existing hard chunk cap was already safely under 2048.

Deliberately not changed: PDF parsing (`pymupdf4llm`, already table/layout-
aware across vendors) and structured extraction (`extractor.py`'s LLM+schema
approach is already vendor-agnostic by design) were reviewed and found
solid. Also didn't add hybrid lexical/BM25 search for exact numbers -
`ChatEngine.get_structured_records` already answers exact-figure questions
via structured extraction, bypassing dense RAG for that case.

## (pending) - 2026-08-30 - feat: dashboard filters, drop Recent Activity, modern icons

The dashboard's out-of-the-box view had no way to narrow to one bill type or
vendor, "Customize widgets" was a bare multiselect with no explanation of
what each widget showed, and the app-wide emoji icons looked inconsistent
next to the new dark mode. Also merged in `feat/ui-ux-revamp`'s dark/light
theme toggle and sidebar/Files/Settings revamp, which existed on a branch
but had never reached `main`.

- `dashboard.py` / `dashboard_data.py`: a global filter bar (bill/data type +
  vendor/provider, via new `distinct_vendors()` / `filter_records()`) drives
  every widget at once; an empty selection falls back to "show everything"
  rather than a blank dashboard. Removed the Recent Activity widget (the
  Files page already covers it). "Customize widgets" is now per-widget
  toggles with a one-line description each.
- Replaced emoji chrome (nav, buttons, headers, alerts, chat avatars) with
  Streamlit's built-in Material Symbols (`:material/...:`) across
  `main.py`, `chat.py`, `upload.py` - bundled with Streamlit, no external
  font/network request, and legible in both themes. Left
  `data_extraction/categories.py`'s emoji labels alone: they feed Plotly
  chart text and `query_analysis`'s keyword matching, neither of which
  renders markdown. Sidebar nav now routes on stable keys instead of
  matching emoji-prefixed label strings.
- Merged `feat/ui-ux-revamp` (dark/light theme toggle via `theme.py`,
  sidebar/Files/Settings revamp, and an anti-hallucination RAG grounding
  fix) and fixed its formatting (`ruff format` had never been run on it).

Verified via Streamlit's `AppTest` harness against a throwaway vault (never
touches `~/.personal_ai_vault`): all 5 pages render with no exceptions.
162/162 tests pass, `ruff check`/`format` clean.

## (pending) - 2026-08-29 - feat: light/dark theme toggle

 The UI was pinned to a light theme (`.streamlit/config.toml` `base = "light"`),
 so it read wrong on a dark OS. Streamlit 1.62 (the pinned version) bakes its
 base theme into the compiled frontend, so there's no first-class *runtime*
 "switch the whole app's theme" call, and `[theme]` only takes effect at server
start - which is also where the localhost security pin lives, so we don't want
to rewrite it. The light/dark choice is instead a **sidebar toggle** that lives
in `st.session_state` and is applied without touching `config.toml`.

- `src/interface/theme.py` (new): single source of truth for the two surface
  palettes and the mode resolution/persistence. `ui_theme_mode` in
   `st.session_state` records the user's choice (default `light`, so existing
   users see exactly what they saw before this existed).
- App chrome: `apply_app_shell()` (called from `main._setup_page`, after the
   light-mode CSS) injects a `<style>` block that repaints the surfaces
   Streamlit exposes - main, sidebar, metric cards, expander borders. Dark
   values carry `!important` and win by cascade order; light is a no-op so
   toggling off cleanly undoes a dark session.
- Charts: `dashboard.py` pulls gridline/font/pie-border/fill from
   `theme.chart_surfaces()` per render, so a dark session paints its Plotly
   figures for a dark backdrop. The categorical/sequential *hue* roles stay
   mode-independent (color always means the same category in both themes).
- `main.py`: imports `theme`, adds the toggle under the brand in the sidebar,
   and calls `apply_app_shell()` in `_setup_page`. `.streamlit/config.toml`
   comment updated: the light pin is now the *default*, overridable per-session.
- `tests/test_theme.py`: mode resolution/persistence, dark-vs-light surface
  differences, and that `apply_app_shell` injects on dark and is a no-op on
   light. Known limitation, not chased: a few Streamlit widget internals are
   still coloured in JS and may keep light styling under dark mode.

## (pending) - 2026-08-29 - feat: anti-hallucination grounding in the RAG path

The chat could answer confidently from a near-miss chunk - ChromaDB's
top-k always returns `k` chunks, so an irrelevant document (cosine
similarity ~0) was handed to the model with no excuse to decline it, and
nothing logged which chunks were considered so a hallucination couldn't be
traced. This makes retrieval *grounding-first* instead of "top-k no matter
what", across every retrieval path.

- `src/config.py`: `DEFAULT_CHAT_TEMPERATURE` (0.3, down from 0.7 - a small
  local model confabulates at the old default) and
  `DEFAULT_MIN_RELATIVE_SCORE` (0.20; a chunk must clear this cosine
  SIMILARITY to count - the main anti-hallucination lever). Both env
  overridable.
- `src/ai_engine/chroma_store.py`: `retrieve_relevant` now over-fetches up
  to `k`, keeps only chunks whose similarity (0..1, via the new
  `distance_to_similarity`) meets `min_relevance` (a near-miss returns `[]`
  rather than a confident-but-wrong answer; threshold `< 0` disables the
  cut), and dedupes to `max_per_document` (default 1) per source document so
  one long bill can't crowd out the others (new `document_id` helper keys on
  `storage_key`; identity-less chunks are always kept). Results come back
  most-similar-first and are recorded on `last_retrieval()` for
  diagnostics.
- `src/ai_engine/rag_engine.py`: threads the two knobs through
  `RAGEngine._get_chroma_store`, now respects `self.top_k` in
  `get_context_for_query` (a hardcoded `k=3` previously ignored it), labels
  each chunk with its source (`_source_label`) so the model can tell chunks
  apart and a hallucination traces to a chunk + score, and adds
  `_log_retrieval` diagnostics (which chunks, and how strong the best was).
- `src/ai_engine/chat_engine.py`: grounding-first `SYSTEM_PROMPT`
  (never guess/estimate from your own knowledge; say "couldn't find it in
  your vault" and suggest what to upload) and a matching no-context
  refusal. `src/ai_engine/ollama_client.py`: `temperature` default now draws
  from `DEFAULT_CHAT_TEMPERATURE`. `src/interface/config.py` + `main.py`:
  Settings slider defaults to the new temperature and the degraded
  Ollama fallback reuses the same grounded system prompt so it can't drift
  into "answer from your own knowledge".
- `requirements.txt`: dropped a stray unused `watchdog` line;
  `tests/test_chroma_store.py` / `tests/test_chat_engine.py`: added
  threshold/dedup/`score`/prompt regression tests (147 pass).

- `requirements.txt`, `src/config.py`, `src/ai_engine/chroma_store.py`,
  `src/ai_engine/rag_engine.py`, `src/ai_engine/chat_engine.py`,
  `src/ai_engine/ollama_client.py`, `src/interface/config.py`,
  `src/interface/main.py`, `tests/test_chroma_store.py`,
  `tests/test_chat_engine.py`

## (pending) - 2026-08-27 - feat: UI/UX revamp across the interface

The interface had grown functional but inconsistent: no app-wide theme
(the dashboard charts assume a light `#2a78d6` palette, but nothing tied
the app chrome to it), a cluttered sidebar (an always-100% "privacy"
progress bar, raw debug buttons, file count as plain text), a Files page
that dumped every category's files into one long scroll, and a Settings
page with two stacked headers, two Save buttons (one of which showed a
success toast but never persisted), and a form that always showed defaults
instead of saved values.

- `.streamlit/config.toml`: added a light `[theme]` whose `primaryColor`
  matches the dashboard's categorical blue, so buttons/sliders/links/charts
  read as one system. `[server]` localhost binding untouched.
- `src/interface/main.py`: set a `page_icon`, added a small global CSS coat
  (calmer spacing, rounded controls, metric values as cards); rebuilt the
  sidebar (brand header, `Documents in vault` metric, one privacy line, and
  diagnostics moved into a `Status & diagnostics` expander); Files page now
  has a name search + a per-category segmented control so one category
  renders at a time instead of a long scroll; Settings page reduced to a
  single header + single primary Save, and now prefills from saved config.
- `src/interface/config.py`: `render_config_page(current=...)` prefills from
  saved values and returns them; dropped the duplicate header and the
  misleading second Save button (persistence is the caller's single action).

- `.streamlit/config.toml`, `src/interface/main.py`,
  `src/interface/config.py`, `CHANGELOG.md`

## 83539b1 - 2026-08-25 - feat: add Dashboard page with configurable widgets

There was no single view aggregating what's in the vault - only a flat Files
list. Adds a Dashboard page (`src/interface/dashboard.py`) with an ordered
`WIDGETS` registry (overview metrics, documents-by-category and
spend-by-category/spend-over-time bar charts, recent uploads), built on
pure, independently-tested aggregation helpers in `dashboard_data.py`
(`count_by_category`, `total_spend`, `spend_by_category`,
`spend_over_time`, `recent_uploads` - deliberately excluding `brokerage`
from spend totals, since its `total` is portfolio value, not spend). Which
widgets are shown is user-configurable via a multiselect, persisted to a
new `dashboard_config` vault key (`{"enabled_widgets": [...]}`), added to
`INTERNAL_VAULT_KEYS` in `src/data_vault/vault.py` so it isn't treated as
an uploaded document.

- `src/data_vault/vault.py`, `src/interface/dashboard.py`,
  `src/interface/dashboard_data.py`, `src/interface/main.py`,
  `tests/test_dashboard_data.py`, `tests/test_data_vault.py`

## 756348e - 2026-08-24 - feat: add multi-session chat management and a context-window meter

There was no way to tell how full the model's context window was, and no
concept of separate chats at all - one growing history per vault, wipeable
only by a full destructive clear. Adds named, switchable, deletable chat
sessions: a `chat_sessions_index` vault record tracks sessions, each with
its own `chat_history_<session_id>` vault key
(`src/interface/chat_sessions.py`); the pre-existing single-conversation
history under the legacy `chat_history` key is auto-migrated (in place,
without copying messages) into a session titled "Previous Chat" the first
time the chat page loads. `src/data_vault/vault.py`'s
`is_internal_vault_key()` now also recognizes the `chat_history_` prefix
and `chat_sessions_index`. Also adds a live progress-bar meter, driven by
`ChatEngine.estimate_usage()` (added in the previous commit), that warns as
the context window fills up.

- `src/data_vault/vault.py`, `src/interface/chat_sessions.py`,
  `src/interface/main.py`, `tests/test_chat_sessions.py`

## daecbab - 2026-08-24 - feat: give chat real conversation memory and wire num_ctx to Ollama

Every chat turn was previously stateless from the model's point of view -
only freshly-retrieved RAG context was ever sent, never prior turns - and
the app's own context-window budget assumption
(`DEFAULT_CONTEXT_WINDOW_TOKENS`) was never actually passed to Ollama via
`num_ctx`, so the two could silently disagree. `ChatEngine` now threads a
budgeted, oldest-first-dropped `history` list into every prompt path
(`query_vault`, `query_vault_stream`, `_build_prompt`,
`_build_structured_prompt`, via new `_fit_history_to_budget`), and derives
all of its own budget math (`_total_budget_chars`) from
`OllamaClient.context_window_tokens` rather than the module-level default.
`OllamaClient` gained a `context_window_tokens` constructor parameter,
passed as `num_ctx` on every `chat()` call. Also adds
`ChatEngine.estimate_usage(history, pending_context="")`, a pure
token-budget calculation (no vault/RAG/Ollama calls) for a future UI
context-window meter.

- `src/ai_engine/chat_engine.py`, `src/ai_engine/ollama_client.py`,
  `tests/test_chat_engine.py`, `tests/test_ollama_client.py`

## 502222c - 2026-08-24 - feat: group uploaded files by category on Files page

Files were a single flat list with no way to tell "T-Mobile bills" apart
from everything else, and a misclassified upload had no way to be fixed
short of deleting and re-uploading. New pure helper
`group_files_by_category()` (`src/interface/file_grouping.py`, kept free of
Streamlit/vault imports for unit testing) buckets `(key, metadata)` file
entries by their `category` metadata, ordered to match
`categories.CATEGORIES` with "other"/uncategorized last and empty
categories omitted. `src/interface/main.py`'s Files page renders these as
per-category sections and adds a re-categorize control per file.

- `src/interface/file_grouping.py`, `src/interface/main.py`,
  `tests/test_file_grouping.py`

## 05e6158 - 2026-08-24 - feat: add mobile/phone bill category to document classifier

T-Mobile and other wireless bills previously fell through to the generic
"other" category (or worse, an unrelated one) since no category recognized
carrier/wireless keywords, so uploads got a useless "What is this
document?" field instead of carrier/account/billing-period fields. Adds a
`mobile` entry to `CATEGORIES` in `src/data_extraction/categories.py`
(keywords: t-mobile, verizon, at&t, wireless, data plan, etc.; fields:
carrier, phone number/account nickname, statement period) - the keyword-first
classifier and Upload-page form pick it up automatically since both are
driven by that one list.

- `src/data_extraction/categories.py`, `tests/test_categories.py`,
  `tests/test_classifier.py`

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
