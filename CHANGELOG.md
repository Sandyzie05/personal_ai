# Changelog

 Commit history for `personal_ai`, newest first, with what actually changed
 and why - kept for future reference so you don't have to reconstruct intent
 from `git log` alone. Update this file whenever you commit.

## (pending) - 2026-11-21 - feat: P1-P8, P12 and Track R (Robinhood) in one MR

- P1 `evals/stress.py`: 56 labelled cases (ambiguous, incomplete, stale, conflict, wrong-record, clean) through
  `service.run_query`; `action_of` is the only action definition; B1 info row; `expected_agents` on golden tasks (76/76).
- P2 verifier relevance: cited record must match the claim's provider/period/category/account; aggregates cite in-quarter
  records; `single_source` caveat; `PlanStep.account` (code-set).
- P3 `workers.ambiguity` + templated clarify question; chat re-plans the follow-up with the question.
- P12 ledger `overdue` → `missing:*` claims + `missing_statement` escalation + Sync now button (click only).
- P6 escalation precision/recall (100%/100%), Accept/Correct/Dismiss review log (ids only), `evals/review_cases.py` stubs.
- P5 `memory.recall`/`forget`, `schemas.CoreFact`, report section + trusted baseline, Forget button (S18).
- P4 composed-leakage eval over golden + stress + service chains; real leak fixed: dependency inputs default-deny (S19).
- P7 `make eval-stability RUNS=10 [LIVE=1]` with 8 seeded perturbations; every hard row 10/10.
- P8 `evals/tot_synth.py` planted-label generator, dev/held-out split, rubric fix (neutral families), `TOT_CONVERGE_SCORE`
  0.8, dev calibration; held-out ToT beats both baselines on accuracy and calibrated Brier (raw Brier: vote better).
  Saved forecasts keep the selected thesis.
- Track R: `forecaster/robinhood.py` (allowlists, arg validation, denylist), fundamentals/ratings/financials/earnings/SEC
  facts/options-implied move/technicals as evidence, Portfolio fit step + tool-less local agent, Fill closes from Robinhood,
  Watchlist/Screens imports, Events earnings calendar, R8 evals (S20). Built against synthetic fixtures.
- Integration: memory recalled before the clarify check; `expense_worker(get_records, get_overdue=, get_memory=)`.

## (pending) - 2026-11-21 - fix: critic consistency, Track step, Events crash; Track R plan

- Events crashed (`StreamlitDuplicateElementKey`) when today's stories and a search were both shown: `agent_chip(key=)`.
- Critics: evidence ids sent as short aliases E1..En and mapped back in code (64-hex ids cost ~30 tokens each and were
  garbled: `unknown_evidence`); rewritten `forecast-critic` skill (what each card family is, stance/confidence bands,
  like-with-like comparisons, length limits) and per-role rules; critic max tokens 700 → 900, cap 1024.
- Skill pack upgrades an installed skill that is still an unedited copy of an earlier shipped version (was: never updated).
- Explainer: aliases too; small whole numbers (<= 31, e.g. "10-year", "3") are wording, not invented data.
- Track (optional): *Fill draft session list* (offline NYSE holiday rules, `forecaster/sessions.py`) + listing-exchange
  picker replace hand-writing JSON; research errors now say why (e.g. exchange mismatch).
- MCP: `decline_` and `set_` are write prefixes (Robinhood labels `decline_trade_approval`/`set_legend_chart_settings` "read").
- Roadmap Track R: Robinhood research integration plan (R0-R8, rules R-S1..R-S5, decisions R-D1..R-D3).

## (pending) - 2026-11-21 - feat: F7 news analyst; news, explainer and watchlist UX; F1 live ✓

- F1 live: ADBE 3/4 critics completed on the 4B. Roadmap §0/§1 + Miro updated; next card P1.
- F7: `schemas.NewsFacts` (closed enums), `builtin-news-analyst` + slot `forecast.news_analyst`, `forecaster/news_analyst.py`
  (one `extract_facts` per headline, counts only, time-bounded). Events: *Analyze with AI* adds an AI-sentiment column, per-ticker
  counts and an AI-vs-keyword disagreement count. Eval row "News analyst: injected text reaching output" 0/190.
- News: Yahoo search returned 0 stories for multi-word topic phrases, so "Fetch today's stories" was empty; topic queries are
  now single words. Search ranks headlines that mention the term (or ticker) first and hides the rest behind a toggle. Stories
  are a table (date, headline, publisher, tickers, sentiment, impact, link) instead of text lines.
- Thesis explainer: "Why this verdict" heading, purpose caption, evidence shown by title, hex ids stripped from prose, prompt
  keeps ids out of the text, summary limit 360 → 500 (was cutting mid-sentence).
- Watchlist "Explain with AI": result/status now persists below the table (was a warning that vanished on rerun).
- Agents page: fixed `agents_group` Streamlit warning (default + session state).

## (pending) - 2026-11-21 - fix: critic JSON robustness, explainer reasons, AI review UI

- Live ADBE run on the 4B: 3/4 critics `not_json`, explainer `schema`. Parser now uses `raw_decode` (ignores a repeated
  object / trailing note) with `strict=False` (raw newlines in strings); oMLX `finish_reason=length` now reports `truncated`.
  Critics record closed diagnostics (`output_chars`, `finish_reason`; never model text). Prompts ask for brevity.
- Explainer: an invented number is now `invented_number` (was lumped into `schema`); prompt says to copy numbers exactly.
- UI: plain-language invalid reasons; diagnostics + thought-tree tables replace raw JSON; clearer rule-based verdict and a
  note when critics were not needed (ToT converged in round 1); explainer purpose caption; no duplicated chip label or
  "Saved forecasts" header.
- Chat: fixed Streamlit warning (`active_agent_id` selectbox set both `index=` and a session-state key).

## (pending) - 2026-11-21 - fix: Forecast page readability + watchlist hiding tickers

- Research "Why these scores": one signal table (input + per-horizon effect, from new `HorizonPrediction.drivers`) and a
  horizon summary table instead of duplicated text per horizon; limitations as a list. "Data sources" table replaces raw
  provenance JSON. Older saved records without `drivers` fall back to deduplicated text.
- Saved-forecasts empty state explains how to save one (outcome tracking → Prepare → Save); behaviour unchanged.
- Watchlist: max-risk filter defaulted to `high`, silently dropping `very_high` tickers (e.g. TSLA). Default now shows all
  risk levels and a caption names every hidden ticker and why.

## (pending) - 2026-11-21 - feat: small default model for AI-review critics

- New `PERSONAL_AI_FORECAST_CRITIC_MODEL` (default `Qwen3-4B-Instruct-2507-MLX-8bit`, from
  `lmstudio-community/Qwen3-4B-Instruct-2507-MLX-8bit`); the 4 `builtin-critic-*` agents default to it. Why: a non-thinking
  4B keeps JSON inside the 700-token cap and 4 calls inside the deadline next to the resident 27B. Explainers unchanged.

## (pending) - 2026-11-21 - docs: session handoff

- `roadmap.md` §00 "Start here": state after PR #25, what the user runs next on the model laptop, a decision table for the next
  card by state-report result, the opencode prompt, and working conventions. F7 rewritten as an executable card (news analyst
  behind quarantine). `user_testing_guide.md` §0: fixed `cd`/`HOME` order; post-pull install + known pip warnings.

## (pending) - 2026-11-21 - feat: Track F - Forecast overhaul on configurable workflow agents

- F1 critics: the model no longer has to echo snapshot/role/scenario/source ids (code fills them; a contradicting echo is still
  rejected); `<think>` blocks and code fences stripped; closed `invalid_reason`; queue-aware budget
  (`FORECAST_SPECIALIST_CALL_SECONDS` x ceil(4 / concurrency), 90 s at concurrency 2); max tokens 400 -> 700; latency per critic.
- F2 built-in agents are tweakable (vault `agent_overrides`, internal key; editable fields only; Reset); Agents tab lists
  My agents / Forecast workflow / Platform.
- F3/F4 `src/agents/slots.py`: fixed default agent per AI step, tools clamped, skills folded in; 4 critic agents + thesis and
  watchlist explainers (`src/forecaster/explain.py`, closed schemas, numbers must come from the input); new skill
  `forecast-critic`; critic agents also used by the Coordinator.
- F5-F9 Forecast page: Research is one flow (evidence + chart -> AI review with agent chips -> verdict -> track); Watchlist with
  sparklines and AI explanation; Events layout; Screens/Drilldown with charts and "Research this ticker"; Track record explains
  what it measures, System health shows configured sources. F10 Chat selector lists every chat-capable system agent + custom.
- Invariant S17 (slot agents). Tests: `test_agent_overrides.py`, `test_slots_explain.py`, new AppTests; 770 pass, `make eval` PASS.

## (pending) - 2026-11-21 - docs: plan Track F (Forecast overhaul on configurable agents)

- `roadmap.md`: Track F (F1-F10) goes before the P-cards (user decision). Workflow slots with fixed default agents that are
  tweakable in the Agents tab (overrides + reset), locked tools/schema/budget per slot, an agent chip on every AI feature,
  dedicated critic/explainer/news agents, per-section Forecast redesign, Chat selector listing every agent. Progress diagram updated.

## (pending) - 2026-11-21 - fix: Forecast tables crash with NumPy 1.26 (pyarrow needs NumPy 2)

- Recommendations, Technical drilldown and Screens crashed in `st.table` with `ImportError: pyarrow requires NumPy 2.0 or newer`.
  `requirements.txt` allowed `numpy>=1.26`, so an existing venv kept 1.26 while pyarrow upgraded. Now `numpy>=2.0`.

## (pending) - 2026-11-21 - docs: record AI-framework review (D13)

- `platform_plan.md` D13: only MCP is used; no submission commits to LangChain/LangGraph/LangSmith/CrewAI; per-framework verdict and
  revisit triggers. `roadmap.md` §6: optional LangGraph parity eval and the W4 MCP thought-tree resource.

## (pending) - 2026-11-21 - feat: `make state-report` (deterministic, redacted)

- The first hand-collected report was wrong: system python instead of the venv (packages "n/a", pytest "missing"), a bare curl
  without the oMLX key reported "not reachable", and "memory pressure" was actually free memory. `scripts/state_report.py`
  now measures these the same way the app does (it loads `.env` like `main.py`); the agent only fills user-reported sections.
  Smoke test `test_state_report.py`.
- Forecast keys: `.env` entries named `FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `SEC_USER_AGENT` were ignored (the app only read the
  `PERSONAL_AI_` names), so FRED/SEC showed `not_configured`. Both names now work; the prefixed one wins.

## (pending) - 2026-11-21 - fix: Yahoo research blocked (HTTP 429); quiet expected MCP auth traceback

- Forecast research for any ticker abstained with `yahoo-chart: transport`. Yahoo answers 429 to httpx's default
  `python-httpx/x` User-Agent (reproduced 3/3; a plain app UA gets 200). Public routes now send a fixed generic UA
  (`PERSONAL_AI_HTTP_USER_AGENT`, default `personal_ai/1.0`: no name, email or host); SEC keeps its contact UA.
  Provider issues now state the HTTP status. Regression test in `test_forecaster_providers.py`.
- `mcp.client.auth.oauth2` logged a full traceback for the app's own non-interactive "authorization required" signal
  (an MCP server bound to an agent but not yet authorized). That record is filtered; real OAuth errors still log.

## (pending) - 2026-11-20 - docs: consolidate docs, add executable roadmap (Week 6 feedback)

- New `docs/roadmap.md`: the single place for remaining work. Capability status table (implemented vs planned vs user-run), order,
  conventions and cards P1-P11, each with why (weekly feedback), current state, build steps, files, accept and don'ts. Covers Week 6
  feedback (repeat-run stability P7, composed / shared-memory leakage P4) and open Week 1-5 commitments (Core Memory recall P5,
  stress set P1, verifier relevance P2, clarifying questions P3, escalation metrics + review log P6, held-out ToT + Brier P8).
- Merged `forecasting_system.md` + `reasoning_design.md` into `docs/forecaster.md` (Part 1 system, Part 2 ToT).
- Removed `docs/capstone_feedback_response.md` (traceability now per roadmap card).
- `platform_plan.md` is now the reference (rules, contracts, metrics, "how success is scored"); its backlog/status moved to the
  roadmap and the 2026-10-25 audit table moved to `security_design.md`. `rag_design.md` §8 compressed to a card-status table.
- `architecture.md` rewritten with Mermaid component and user-flow diagrams and a framework-mapping table. README, AGENTS.md
  and source-comment doc references updated. No code behaviour changed.
- New `docs/state_report.md`: redacted, paste-only state report from the model laptop (template, commands, never-include list,
  PII self-check). Removed absolute home paths from public docs.

## (pending) - 2026-11-02 - feat: Week 4 Tree-of-Thought forecaster; merge PR #17 hardening

- Merge: `feat/agent-platform` forked before `main`'s 9d955f9 and lacked atomic/flocked auth writes, offline test isolation and the auth/vault-lock/RAG-privacy/ingestion regressions. Merged back. Wrapping format is unchanged (existing auth files still unlock). One fail-closed RAG rebuild path; source-identity chunk ids.
- ToT: `src/forecaster/tot.py` adds a beam search over competing bull/base/bear theses. The beam keeps one branch per scenario, and the specialist swarm acts as a lazy shared critic. Pruning reasons are recorded; ties break conservatively; the search abstains when nothing is strong enough. The coordinator emits `thesis:<SYM>`, marked uncertain when the result is close, has no critic, or clashes with the heuristic. Design: `docs/reasoning_design.md`.
- Gaps fixed: forecaster step timeout (30 s) was shorter than its own 45 s + 60 s budgets; the LLM planner could choose its own step timeouts; the swarm verdict and the heuristic outlook were never reconciled.
- Hardware budget: app-wide cap on in-flight local requests (`PERSONAL_AI_INFERENCE_CONCURRENCY`, default 2, both streamed and non-streamed chat), plus an optional smaller `PERSONAL_AI_OMLX_WORKER_MODEL` for specialists, quarantine, verifier and planner. See the memory-budget section in `docs/ai_engine.md`.
- Evals: ToT 8/8 vs linear 6/8 vs critic-vote 7/8 on labeled synthetic cases; 5 of 8 converged with 0 LLM calls. Tests: 752 pass. Not run on live oMLX.

## (pending) - 2026-10-25 - feat: RAG R0-R6, ingestion R7-R10, orchestration backlog B1-B16

- RAG: chunk-per-line fix, contextual headers, batched/cached embeddings (encrypted vault cache), hybrid dense+BM25 with RRF, evidence-id context, abstention without a model call, deterministic grounding check; collection `personal_ai_vault_v4` (rebuild index). `make eval-rag`: recall@5 0.445 to 1.0, context precision 0.11 to 0.93 (stand-in embedder). Fixed provider-scoped retrieval returning nothing (flat `where`).
- Ingestion: `validate_pdf`, manifest/ledger/dedupe/supersede, headless `run_ingest`, Sync panel, Gmail attachment fetcher (read-only), Playwright portal recipe framework (mock-portal tested; no real provider recipes).
- Platform: Scheduled tab + flock, Gmail/brokerage workers, single-use approval tokens, selectable specialists, optional LLM planner (off), verifier, cancel tokens, expense accuracy (partial quarters), forecaster high/low/volume, oMLX client fixes (streamed tool calls, json_schema), `make eval-live`, `evals/userguide.py`.
- Tests: 635 pass; `make eval` and `make eval-rag` PASS. Not verified on live oMLX.

## (pending) - 2026-10-25 - docs: consolidation, user testing guide

- `docs/` consolidated: `system_architecture.md` + `development_planning.md` merged into `architecture.md`; `ai_engine.md` rewritten oMLX-first
  (removed the stale llama-index/Ollama setup and an example that stored a password in the vault); `forecasting_system.md` updated to the real
  modules; `multi_agent_design.md` trimmed to the runtime reference (superseded planning sections replaced by pointers); `security_design.md` gained
  an at-a-glance table and contents; new `docs/README.md` index. Broken README links fixed.
- New `docs/user_testing_guide.md` (RAG, agents, predictions, security spot checks) and `scripts/make_test_bills.py` (9 synthetic PDFs incl. a
  conflicting bill and a hostile PDF).
- Found while writing the guide: `expense_worker` mixes categories and does not flag partial quarters (backlog B15).
- Removed three leftover agent worktrees/branches.

## (pending) - 2026-10-25 - docs: RAG diagnosis, design and plan (`docs/rag_design.md`)

- Documented the actual encoder, chunking, similarity and context assembly, with the root causes of inaccurate/hallucinated answers
  (chunk-per-line fragmentation, no contextual headers, no Qwen query instruction, dense-only retrieval, permissive 0.20 threshold,
  no grounding check, no incremental indexing, no retrieval eval). Plan R0-R11 includes Gmail attachment ingestion and a constrained
  browser-agent design for portal bill downloads (deterministic recipes, credentials never given to an LLM, human-in-the-loop 2FA).
  Decision recorded: Advanced RAG inside the document retriever, Modular only at coordinator level (platform_plan D8, rag_design §8.1).
  Added `.idea/capstone-week-3.md` (retrieval checkpoint). No code changed for this entry.

## (pending) - 2026-10-25 - feat: multi-agent platform, stock_predictor merge, oMLX-only

- oMLX default; Ollama gated by `PERSONAL_AI_OLLAMA_ENABLED`; OpenAI-shape tool-call adapter; saved Ollama configs migrate;
  "Rebuild RAG Index" now truly drops and re-embeds (`ChromaStore.reset`).
- Ported stock_predictor logic as pure services: `forecaster/{technical,recommend,screens,news,report,health}.py`; Forecast
  page sections (Recommendations, Validation, Events, Drilldown, Screens). Price-only proxies; no fundamentals/volume.
- `src/agents/orchestration/`: typed schemas, scoped bus, quarantine, Core Memory gate, parallel coordinator, workers,
  Gmail read-only + portal interface, scheduler, request policy (refusals), built-in agent templates + `skills/` pack.
- `evals/` + `make eval`; fixes found by it: quarantine accepted plain-English injections, stale data served as fresh,
  conflicting records produced confident output, duplicate statements double-counted, ungrounded budget claim.
- Docs: `docs/platform_plan.md` (executable plan), `docs/capstone_feedback_response.md`, security_design changelog.
- Independent audit (24 findings) fixed with regression tests; see `docs/platform_plan.md` §6.2. Highlights: Core Memory keys
  were RAG-indexable (now internal), free text from untrusted sources can no longer reach summaries, per-step coordinator
  timeouts with immutable results, oMLX loopback-only, Ollama gate not bypassable, vault only touched on the calling thread.
- Known: 3 pre-existing failures in `tests/test_chroma_store.py`; oMLX untested against a live server; no live portal scraping.

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
