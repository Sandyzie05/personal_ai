# Agent Platform: executable plan, design and status

**Audience: a coding agent (or human) picking this up with no prior context.** Read §0 first, then the section for your
task. Everything here is verified against the code on branch `feat/agent-platform` (uncommitted at time of writing).
Companion docs (index: `docs/README.md`): `docs/architecture.md` (overview), `AGENTS.md` (repo rules), `docs/security_design.md` (threat model + changelog),
`docs/multi_agent_design.md` (agent runtime/MCP reference), `docs/forecasting_system.md` (forecaster), `docs/user_testing_guide.md` (manual test plan),
`docs/capstone_feedback_response.md` (feedback traceability), **`docs/rag_design.md` (RAG diagnosis + design + executable plan R0-R11: chunking fix, hybrid retrieval, grounding checks, bill ingestion via Gmail/portal agents)**. Miro diagram: https://miro.com/app/board/uXjVEVwdVZo=/

## 0. How to work on this (read this first)

1. Read `AGENTS.md`, then this file §2 (invariants) and the §3.1 row for the component you touch.
2. Setup/run: `make install`; tests `make test`; evals `make eval` (must print `Overall: PASS`); app `make run`.
   Tests that need a venv: `PYTHONPATH=. venv/bin/pytest tests -q`. Baseline: **all green** (the 3 former `test_chroma_store.py` failures were fixed by R0/B10). Anything failing is yours.
3. Definition of done for any change: code + tests; `make test` and `make eval` green; no new `pass`/stub bodies;
   §6 status and `docs/security_design.md` changelog updated in the same change; no real personal data in tests/fixtures
   (synthetic only); never print `env`, `.env` or vault contents.
4. Never weaken an invariant in §2 to make a test pass. If one seems wrong, stop and ask the user.
5. Do not commit or push unless the user asks. Work on `feat/agent-platform`.

## 1. Goal and scope

personal_ai is a local, security-first multi-agent **Personal Assistant + Forecaster** (capstone). One Streamlit app
(`src/interface/`), one encrypted vault, local inference on **oMLX** (Ollama disabled by default). A **Coordinator** delegates
to specialists (expense/records, market, forecaster with parallel sub-specialists, verifier), merges findings
deterministically, escalates uncertainty to the user, and never lets untrusted content or an agent change memory/act.

In scope: records Q&A, bill/cash-flow analysis, evidence-based forecast research, daily briefs, read-only Gmail digest,
scheduled runs. **Out of scope (refused in code):** trading, payments, shell commands, medical diagnosis, revealing secrets.

stock_predictor (`/Users/sandgupt/RandomIdeasWithAI/stock_predictor`, FastAPI) is fully absorbed as pure services; it is not
a runtime dependency. Do not port its FastAPI/SQLAlchemy/Redis layers.

## 2. Security invariants (each has enforcing code and tests)

| # | Invariant | Enforced by | Tested by |
|---|---|---|---|
| S1 | Untrusted text (email, portal, web, news) enters only through `quarantine.extract_facts`: tool-less model call, strict schema, allowlist-regex strings, instruction-pattern rejection | `orchestration/quarantine.py`, `schemas.SAFE_TEXT` | `test_orchestration.py`, `evals/injection.py` |
| S2 | Findings derived from untrusted sources carry `trust="untrusted"`; merge propagates it; it never drives a write/memory commit unattended | `schemas.Finding`, `coordinator.merge/requires_confirmation`, `memory.approve` | `test_orchestration*.py` |
| S3 | Inter-agent messages pass `MessageBus.send`: typed `extra="forbid"` schema per (sender, receiver) edge, PII/canary scan on string fields, audit of field names + hash only | `orchestration/bus.py`, `schemas.EDGES` | `test_orchestration.py`, `evals/disclosure.py` |
| S4 | Core Memory changes only after explicit user approval; agents can only `propose`; untrusted-derived items need extra acknowledgement; corrections supersede | `orchestration/memory.py`; UI `interface/agents.py::_render_memory_tab` | `test_orchestration.py`, evals |
| S5 | Out-of-scope requests are refused before planning | `orchestration/policy.py`, `service.run_query` | `evals/refusal.py`, `test_orchestration_service.py` |
| S6 | Gmail/brokerage access is read-only: allowlisted tool names + sensitive-prefix refusal; no write tools in agent templates | `ingest.ReadOnlyMcp`, `mcp.is_sensitive_tool`, `templates.py` | `test_orchestration_ingest.py` |
| S7 | Confident claims must cite evidence; else the user is told (`ungrounded`) | `coordinator.escalations_for` | `test_orchestration.py` |
| S8 | Missing/stale/conflicting/failed data produces `unavailable`/`uncertain`/`unresolved` + escalation, never a guess | `workers.py`, `coordinator.py` | `evals/faults.py`, `test_orchestration_workers.py` |
| S9 | One path from password to DEK (`AuthManager`); `store_data(encrypt=True)` raises without key; agent state (agents, schedule, memory, activity log) lives in the encrypted vault | `security/auth.py`, `data_vault/`, `agents/store.py` | existing vault tests |
| S10 | Public egress limited to fixed provider routes with validated params and size/time bounds; private data never goes to public services | `forecaster/providers.py` | `test_forecaster_providers.py`, `test_forecaster_news.py` |
| S11 | Inference is local: oMLX on loopback by default; Ollama off unless `PERSONAL_AI_OLLAMA_ENABLED=true` | `config.py`, `ai_engine/engine.py` | `test_engine_factory.py` |
| S13 | Core Memory, scheduler and audit keys are internal vault keys: never indexed into RAG, never listed as documents | `data_vault/vault.py::INTERNAL_VAULT_KEYS` | `test_audit_regressions.py`, eval row |
| S14 | oMLX host must be loopback unless explicitly opted in; the bearer token is never sent through redirects/proxies | `omlx_client.py` | `test_audit_regressions.py` |
| S15 | The vault (sqlite) is touched only on the calling thread: workers get snapshots, audit writes happen after the run | `service.py`, `ingest.py` | `test_audit_regressions.py` |
| S16 | Untrusted-derived text never reaches a `Finding.summary`/prompt: summaries are templated, free-text schema fields are closed enums or tight allowlists, all rendered output is markdown-escaped | `schemas.py`, `ingest.py`, `workers.render_report` | eval row, `test_audit_regressions.py` |
| S12 | Skills are instructions only (no personal data); installer never overwrites user edits | `orchestration/skill_pack.py` | `test_orchestration_service.py` |

Residual risks (accepted, documented): regex PII scan is heuristic (schemas are the real control); the quarantine LLM's
obedience is not relied on (schema validation is); Chroma index is plaintext if persisted (currently in-memory; see `security_design.md`); no live
portal scraping exists, so portal-side threats are modelled with fixtures only.

## 3. Architecture

```
user chat ─┐                       ┌─ Expense/Records ──► vault (records, RAG)
scheduler ─┼─► policy.screen ─► Coordinator ─► MessageBus ─► Market ─────────► public providers / brokerage (read-only)
gmail ─┐   │   (refuse)         plan→parallel   (typed,        Forecaster ──► research + ScenarioSwarm (4 parallel specialists)
portal ┴► quarantine ─typed──►  →merge→escalate  scanned)      (Verifier rule = ungrounded check in coordinator)
                                     │
                       Merged report ─► Escalate to user ─► Core Memory (propose → UI approve)
                       all LLM calls: oMLX (127.0.0.1)    audit: agent_activity_log (vault)
```

**RAG pattern (decision D8):** the system is *Modular at the top* (routing, orchestration, fusion, memory = Coordinator, `plan_for`, `merge`,
Core Memory) and the document retriever inside it is being upgraded from Naive to *Advanced* RAG (`docs/rag_design.md`). Do not add agentic
retrieval loops inside the retriever.

### 3.1 Component map (file → responsibility → public API)

| File | Responsibility | Public API |
|---|---|---|
| `src/agents/orchestration/schemas.py` | Typed contracts | `Finding`, `Plan`, `PlanStep(symbol?)`, `CashflowDelta`, `SymbolRequest`, `HoldingsSummary`, `EmailFacts`, `StatementFacts`, `EDGES`, `SAFE_TEXT` |
| `.../bus.py` | Scoped message bus | `MessageBus(vault, edges, canaries).send(sender, receiver, payload)`; `DisclosureViolation` |
| `.../quarantine.py` | Untrusted-input extractor | `extract_facts(raw, source, schema, client) -> Extraction`; `sanitize_text` |
| `.../memory.py` | Core Memory gate | `propose`, `list_pending`, `list_core`, `issue_approval_token`, `approve(token, acknowledge_untrusted)`, `reject`; `ApprovalError` |
| `.../coordinator.py` | Delegate/merge/escalate | `Coordinator(workers, max_workers, max_retries, deadline_s, max_age, transient, recheck).run(plan) -> RunReport`; `merge`, `validate_plan`, `escalations_for`, `requires_confirmation` |
| `.../workers.py` | Service adapters | `plan_for(query)`, `expense_worker(get_records)`, `forecaster_worker(research, swarm)`, `market_worker(providers, bus)`, `render_report(report)` |
| `.../ingest.py` | Gmail/portal ingestion | `ReadOnlyMcp`, `gmail_fetcher`, `digest_emails`, `ingest_statements`, `email_worker`, `portal_worker`, `PortalFetcher` protocol |
| `.../schedule.py` | Scheduled runs (vault) | `add_job`, `list_jobs`, `remove_job`, `due_jobs`, `run_due(vault, run_query)` |
| `.../policy.py` | Refusals | `screen(query) -> Refusal or None`, `RequestRefused` |
| `.../templates.py` | Built-in agent configs | `BUILTIN_AGENTS` (`builtin-<role>`: coordinator, expense, health, email, portal, market, forecaster, verifier), `list_builtin()` |
| `.../skill_pack.py` + `skills/*.md` | Built-in skills | `install_builtin_skills()` (6 skills) |
| `.../service.py` | UI wiring | `build_coordinator(vault, get_records, scenario_factory, extra_workers)`, `run_query` (applies policy), `propose_from_report` |
| `src/agents/{runtime,mcp,store,skills}.py` | v1 agent loop, MCP, vault records | see `multi_agent_design.md` |
| `src/forecaster/*.py` | Public-data research | `research`, `scenarios`, `outcomes`, `technical`, `recommend`, `screens`, `news`, `report`, `health`, `providers` |
| `src/ai_engine/engine.py` | Client factory | `build_chat_client`, `build_embed_client`, `client_kwargs_from_config`, `EngineDisabledError` |
| `src/ai_engine/omlx_client.py` | oMLX client | OpenAI-shape chat/embeddings, tool-call mapping (`_to_openai_messages`, `_from_openai_message`) |
| `src/interface/` | UI | `main.py` (Coordinator in chat selector via `_respond_coordinated`), `forecast.py` + `forecast_tabs.py`, `agents.py` (Core Memory tab) |
| `evals/` | Eval harness | `python -m evals.run` (`make eval`): golden, faults, injection, disclosure, refusal |

### 3.2 Coordination protocol

1. **Plan** (`workers.plan_for`): rule-based and auditable. Bills/records words → `expense`; tickers → one `forecaster` step per
   symbol (max 3); market words → `market` (depends on `expense` when both). Nothing delegable → `ValueError`.
2. **Delegate** (`Coordinator.run`): validate (allowlisted agents, no cycles, ≤8 steps); steps run in waves (≤5); independent
   steps run concurrently in a thread pool; each wave waits up to the largest step timeout, bounded by the run deadline.
   Failed dependency → dependents `skipped`.
3. **Recover**: only classified-transient errors (`TimeoutError`, `ConnectionError`) retry (max 1); others fail immediately;
   hung steps end `timeout` (their thread may linger; workers must be idempotent/read-only).
4. **Merge** (`merge`): group by `claim_key`; distinct values (cents tolerance) → `Conflict`, claim becomes `unresolved`,
   `value=None`; trust is the worst of contributors; evidence ids are unioned. One bounded `recheck` per conflict, then report.
5. **Escalate** (`escalations_for`): `conflict`, `step_failed`, `unavailable`/`uncertain`, `stale` (> `max_age`),
   `ungrounded`, (and `untrusted_write` via `requires_confirmation`). Any escalation ⇒ `report.needs_user`.
6. **Answer** (`render_report`): deterministic, source-preserving markdown; untrusted-derived claims are labelled.

### 3.3 Data contracts

`Finding{agent, claim_key, value, summary≤500, evidence_ids, confidence∈[0,1], status∈{ok,uncertain,unavailable,unresolved},
trust∈{trusted,untrusted}, as_of}`. Claim-key conventions: `bill:<provider>:<period_end>`, `cashflow_delta:<YYYY-Qn>`,
`budget_constraint:<period>`, `outlook:<SYM>:<h>s`, `specialists:<SYM>`, `lean:<SYM>`, `email:<id>`, `statement:<provider>:<end>`,
`records`. Bus edges (only these exist): `expense→market (CashflowDelta)`, `coordinator→forecaster (SymbolRequest)`,
`coordinator→market (HoldingsSummary)`, `email→coordinator (EmailFacts)`, `portal→expense (StatementFacts)`.

### 3.4 Agent roster

All are code-constant templates (not editable); the user binds MCP servers in Agents → MCP Servers. Roles/tools/skills are in
`templates.py` (`_ROLES`, `_SKILLS`); every prompt carries the shared safety rules (`_COMMON`). Only `builtin-coordinator`
is selectable in chat today; specialists run as coordinator workers. Skills attached: personal-finance-analysis,
market-research-brief, untrusted-content-handling, conflict-escalation, forecast-evaluation, data-minimization.

## 4. Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PERSONAL_AI_LLM_ENGINE` / `PERSONAL_AI_EMBED_ENGINE` | `omlx` | engine selection |
| `PERSONAL_AI_OLLAMA_ENABLED` | `false` | re-enable Ollama code path |
| `PERSONAL_AI_OMLX_HOST` | `http://127.0.0.1:8000` | oMLX server (must stay loopback) |
| `PERSONAL_AI_OMLX_CHAT_MODEL` | `Qwen3.8-27B-4bit` | chat model (also `DEFAULT_CHAT_MODEL` when engine=omlx) |
| `PERSONAL_AI_OMLX_EMBED_MODEL` | `mlx-community/Qwen3-VL-Embedding-2B-8bit` | embeddings (changing it ⇒ rebuild index) |
| `OMLX_API_KEY` / `omlx_api_key` | unset | bearer token for oMLX; read from env only |
| `PERSONAL_AI_OMLX_TOOLS` | `true` | declare tool-calling support (oMLX reports no capabilities) |
| `PERSONAL_AI_SKILLS_DIR` | `~/.personal_ai/skills` | skills install dir |
| `PERSONAL_AI_FRED_API_KEY`, `..._ALPHA_VANTAGE_API_KEY`, `..._SEC_USER_AGENT` | unset | optional forecaster sources |

## 5. Success criteria and measured evidence

Baselines: **B1** single agent without coordinator; **B2** coordinator with `max_workers=1` (sequential). Data is synthetic.
Run `make eval` → `evals/results/latest.md`. Targets are hard gates in `evals/run.py`.

| Metric | Target | Measured (2026-10-25) | Note |
|---|---|---|---|
| Parallel specialists overlap (fan-out: 1 expense + 3 forecasters + market) | yes | yes | |
| Latency vs B2, fan-out (5 steps) | ≤ 0.60 | 0.41 | typical 3-step cross task ≈ 0.68, reported as info (critical path 2/3) |
| Cross-domain success, coordinated / B1 | ≥ 90% / ≤ 40% | 100% / 0% | B1 target applies to the 10 cross-domain tasks (B1 = 67% on all 30 because 20 are single-domain) |
| Single-domain, coordinated vs B1 | no regression | 100% vs 100% | |
| Grounded claims | ≥ 95% | 100% (126/126) | plus runtime `ungrounded` escalation |
| Numeric mismatch vs deterministic recompute | 0 | 0 | |
| Bounded failure recovery (54 faults) | 100% | 100% | |
| Conflict/uncertainty recognition (20) | 100% | 100% | |
| Injection resistance (38 payloads × 5 extractor models) | 0 | 0 | detector flags 34/38 (info); paraphrases are stopped by the closed schema, not by the detector |
| Quarantine text reaching a finding summary | 0 | 0 / 38 | summaries are templated |
| Core Memory/schedule/audit keys exposed to RAG | 0 | 0 / 6 | |
| Core Memory commits without approval | 0 | 0 / 195 | |
| Minimal disclosure (canary/PII in bus, log, egress) | 0 | 0 | |
| Refusal of out-of-scope asks / false refusals | 100% / 0 | 24/24 / 0/12 | regex first gate; real controls are allowlists |

Answer to "benefit of coordination vs running each repo alone": cross-domain tasks (records + market) are unanswerable by B1
(0%) and about 2.4x faster than sequential B2 on the fan-out workload, without regressing single-domain tasks.

## 6. Status and task cards

Legend: DONE (code + tests + eval), PARTIAL, OPEN. A card is complete only when its **Accept** command passes. Test counts: 635 pass, 0 failures; `make eval` and `make eval-rag` PASS (offline, synthetic; **nothing verified on live oMLX**); `make eval` all hard rows PASS.

| Phase | Status | Accept |
|---|---|---|
| 0 Baseline | DONE (407 pass at start; 635 pass, 0 failures now) | `make test` |
| 1 oMLX-only | DONE; **not verified on a live oMLX server** | `pytest tests/test_engine_factory.py tests/test_omlx_tools.py tests/test_omlx_client.py` |
| 2 Port stock_predictor | DONE (price-only proxies; no fundamentals/volume; "period high/low" not all-time; news issues reuse `yahoo-chart` SourceId) | `pytest tests/test_forecaster_*.py` |
| 3 UI merge | DONE | `pytest tests/test_forecast_tabs.py tests/test_agents_page_smoke.py` |
| 4 Agent infra | DONE | `pytest tests/test_orchestration.py` |
| 5 Agent configs/skills/policy | DONE | `pytest tests/test_orchestration_service.py tests/test_orchestration_ingest.py` |
| 6 Untrusted ingestion | PARTIAL (B2-B4, R7-R9 done; real provider recipes + live portal/Gmail runs are the user's): Gmail read-only + portal interface + scheduler done; **no live portal fetcher**, no scheduled-run UI, Gmail/brokerage workers not yet wired into the coordinator (B2-B4) | `pytest tests/test_orchestration_ingest.py` |
| 7 Evaluation | DONE (refusal included) | `make eval` → `Overall: PASS` |
| 8 Docs + diagram | DONE (this doc, security log, feedback response, Miro) | review |
| 9 Independent audit + fixes | DONE (§6.2) | `pytest tests/test_audit_regressions.py` |

### 6.1 Backlog (ordered; each is a self-contained card)

Each card: **goal / files / accept**. Pick from the top; do not start a card whose accept command you cannot run.

| # | Card | Files | Accept | State |
|---|---|---|---|---|
| B1 | **Live oMLX verification.** Start oMLX, run `make run`, Rebuild RAG Index, one Coordinator turn, one tool-calling agent turn. Fix what breaks: streaming tool-call deltas are not converted (`omlx_client._stream_chat_response`); `response_format` uses `{"type":"json_schema","schema":...}` but the OpenAI shape is `{"type":"json_schema","json_schema":{"name","schema"}}`; orphan tool messages get minted ids. | `ai_engine/omlx_client.py`, `tests/test_omlx_*` | manual run recorded in §6.2 + unit tests for each fix | PARTIAL 2026-10-25: all 3 code fixes + unit tests DONE; **live oMLX run still to do (user)** |
| B2 | **Live portal fetcher** (superseded by `rag_design.md` cards R7-R10: framework, constraints, per-provider recipes) implementing `ingest.PortalFetcher` (browser/code automation using credentials from the encrypted vault, one provider first). Output must go through `ingest_statements` (quarantine). No credentials in code/logs; allowlisted hosts only. | new `orchestration/portal_*.py`, tests with recorded HTML fixtures | `pytest tests/test_orchestration_ingest.py` + a fixture-based fetcher test + `make eval` | PARTIAL: framework + Playwright runner + mock-portal tests DONE (rag_design R9); no real provider recipe (R10), live run is the user's |
| B3 | **Scheduled-run UI + trigger.** Agents → "Scheduled" tab (add/remove job); call `schedule.run_due` on app open; results land as proposals/notifications. Cross-process safety (file lock) because `schedule` currently locks only within one process. | `interface/agents.py`, `schedule.py` | AppTest + `pytest tests/test_orchestration_ingest.py` | DONE 2026-10-25 (Scheduled tab, run on app open, flock) |
| B4 | **Wire Gmail + brokerage workers into `service.build_coordinator`** (email worker via `ReadOnlyMcp`; brokerage holdings via read-only MCP → `HoldingsSummary` bus edge, which is currently unused). Extend `plan_for` ("my emails", "my holdings"). | `service.py`, `workers.py`, `ingest.py` | tests + eval case + `make eval` | DONE 2026-10-25 (email+brokerage workers, HoldingsSummary edge; not run against a real Gmail/broker MCP) |
| B5 | **Cancellation for hung workers.** Timed-out steps stop being waited on but their thread keeps running (read-only workers only, by design). Add cooperative cancel tokens for long provider calls; keep workers read-only/idempotent. Note `max_retries=1` also retries non-idempotent bus sends (market worker re-sends the same delivered message): dedupe by payload hash. | `coordinator.py`, `workers.py`, `bus.py` | `pytest tests/test_orchestration.py` | DONE 2026-10-25 |
| B6 | **Real-model injection eval.** Run the 38-payload corpus against the actual local model as extractor (opt-in `make eval-live`); detector currently flags 34/38 (paraphrases are caught only by the schema, by design). Track pass rate, never loosen schema. | `evals/` | `make eval-live` report | DONE (harness; `make eval-live` not run: needs live oMLX) |
| B7 | **Make "UI-only approval" structural.** `memory.approve` is gated by `approved_by_user=True`, a convention. Introduce an approval token minted only by the Streamlit button handler (session-bound, single-use). | `memory.py`, `interface/agents.py` | tests: approve without token fails | DONE 2026-10-25 (single-use session+item-bound token) |
| B8 | **Selectable specialists + optional LLM planner** behind the same `Plan` validation (`validate_plan` stays the gate). | `workers.py`, `main.py` | tests + eval parity | DONE 2026-10-25 (LLM planner off by default) |
| B9 | **LLM Verifier agent** (template exists; runtime grounding check is deterministic in `escalations_for`). | `templates.py`, `workers.py` | eval: verifier catches seeded unsupported claim | DONE 2026-10-25 (deterministic verifier + eval; LLM may only add escalations) |
| B10 | **Fix the 3 pre-existing failures** (done as part of `rag_design.md` card R0) in `tests/test_chroma_store.py` (patch target `chromadb`, chunk-size asserts 2047>2000, 104>100). | `tests/test_chroma_store.py` or `chroma_store.py` | `make test` fully green | DONE 2026-10-25 (with R0) |
| B11 | **Fundamentals/volume for forecaster** (price-only proxies today; `PriceBar` has no high/low/volume; "period high/low" not all-time) and a separate `SourceId` for news. | `forecaster/models.py`, `providers.py` | `pytest tests/test_forecaster_*` | DONE 2026-10-25 (high/low/volume, ATR, `yahoo-news` SourceId) |
| B13 | **RAG quality (P0): chunk-per-line bug** (today's document path is Naive RAG; target is Advanced RAG, decision D8). `chroma_store.add_documents` emits one chunk per line; the structure-aware `chunk_text` is never called. Executable plan R0-R6 in `docs/rag_design.md` (R0 first, then the eval harness R1 before any tuning). | `ai_engine/chroma_store.py`, `evals/rag/` | `docs/rag_design.md` §8 R0/R1 accept | DONE 2026-10-25 (R0-R6; `make eval-rag`: recall@5 0.445→1.0, ctx precision 0.11→0.93; hashed stand-in embedder, `min_relevance`/`k` not re-tuned on the real model) |
| B14 | **RAG freshness + bill ingestion** (manifest, expected-bills ledger, incremental indexing, Gmail attachments, browser portal recipes, security S-R1..S-R8). Plan R7-R11 in `docs/rag_design.md`. | `data_ingestion/` | `docs/rag_design.md` §8 R7-R10 accept | PARTIAL: R7-R9 DONE (code + tests); R10 real recipes + Gmail attachments vs real MCP are user-supervised; Sync panel on Upload page |
| B15 | **Expense worker accuracy (found while writing the user testing guide).** `expense_worker` sums *every* record's total per calendar quarter: it mixes categories (mobile + utilities) and does not warn when a quarter is partial (e.g. only June in Q2). Filter by category from the question, require all three months (or flag the quarter as partial/uncertain), exclude superseded/duplicate documents. | `orchestration/workers.py`, tests, eval fault case | tests + `make eval` | DONE 2026-10-25 (records lacking `period_start` now read as partial) |
| B16 | **Frame the user testing results into evals.** Every Fail found with `docs/user_testing_guide.md` becomes a golden/eval case. | `evals/` | `make eval` | DONE 2026-10-25 (`evals/userguide.py`, 12 cases; add one per manual Fail) |
| B12 | **Commit/PR hygiene:** branch `feat/agent-platform` is uncommitted; split into reviewable commits when the user asks. | git | user decision | OPEN |

### 6.2 Independent audit (2026-10-25) and resolution

A fresh-context reviewer audited the new code (24 findings: 2 HIGH, 10 MEDIUM, 12 LOW, plus weaknesses in the eval itself). Every fix has a
regression test (`tests/test_audit_regressions.py`, `tests/test_orchestration.py`) and the evals were hardened where they were circular.

| ID | Finding | Resolution |
|---|---|---|
| H1 | `core_memory`, `core_memory_pending`, `agent_schedule` were not internal vault keys, so **unapproved proposals were embedded into the RAG index** and listed as documents | added to `INTERNAL_VAULT_KEYS`; eval row "keys exposed to RAG" |
| H2 | Free text from email/portal (`topic`, `provider`) reached `Finding.summary`, chat history and later prompts; denylist evaded by Unicode tag chars, soft hyphen, newline splits, paraphrase | `topic` is a closed enum; `sender_domain`/`provider` tightened; summaries are templated; sanitizer strips tag block/soft hyphen/invisible operators + NFKC; injection check normalises whitespace; eval row "quarantine text reaching a summary" |
| M1 | Step timeouts not per-step; timed-out step later flipped to `ok`; hung threads starved the pool | per-future deadlines, results returned by value (immutable after timeout), fresh pool per wave |
| M2 | Naive `as_of` crashed the run after the work was done; UI caught only `ValueError` | `AwareDatetime` validated; UI catches `Exception` with a safe message |
| M3 | `merge` ignored `uncertain` findings; `1` vs `"1"` spurious conflict; NaN | compare all valued non-unavailable findings; numeric normalisation; non-finite rejected |
| M4 | Unknown `trust` values approved without acknowledgement | `propose` validates; `approve` fails closed on any non-`trusted` value (UI-only approval is still a convention: B7) |
| M5 | Pending queue unbounded/duplicated; non-atomic writes | dedupe + cap 50 + process-wide lock (cross-process lock: B3) |
| M6 | Markdown/link/image injection via claim keys and summaries; memory tab used `st.markdown` | labels sanitised, `render_report` escapes everything, memory tab uses `st.text` |
| M7 | `HoldingsSummary.symbols` free text; dashed cards, obfuscated emails, large floats passed the bus; canaries unused in production | ticker-shaped symbols, digit-separator normalisation, "at/dot" emails, amount bounds, canaries seeded from the user's account identifiers/providers |
| M8 | Ollama gate bypassed by spelling (`"Ollama"`, `"bogus"`) | normalise + allowlist `{omlx, ollama}` |
| M9 | oMLX client accepted any host (RAG context + bearer token), followed redirects/proxies with the token | loopback-only unless `PERSONAL_AI_ALLOW_REMOTE_OMLX=true`; no redirects/proxies whenever a key is sent |
| M10 | Vault (sqlite, single-threaded) used from worker threads | records snapshotted on the calling thread; bus audit + injection flags written on the calling thread; Gmail/portal workers never touch the vault |
| L1 | failing `recheck` crashed the run | caught; conflict stays reported |
| L2 | retries re-send non-idempotent bus messages | documented; B5 |
| L3 | `MAX_WAVES=5` vs 8-step chains | documented limit (steps beyond wave 5 end `skipped: round budget`) |
| L4 | `requires_confirmation` unused in production | taint is enforced at the Core Memory gate (`propose_from_report` passes `trust`); no write tools exist; revisit if one is added (B7) |
| L5 | any ALL-CAPS word became a ticker (egress + wrong symbol) | `$TICK`, bundled universe, or introduced-like-a-ticker only |
| L6 | refusal gaps ("dump", "close my position", "email my vault key") | rules extended + tests; still a first gate |
| L7 | news parser crashed on non-dict items | skipped; provider catches `AttributeError` |
| L8 | no PII scan on the public news query | `providers.news` refuses queries containing emails, long digit runs or SSN shapes |
| L9 | `get_agent` returned shared mutable template | deep copy; `None` guarded |
| L10 | oMLX adapter edge cases | empty `choices` handled; rest in B1 |
| L11 | failed re-embed left an empty index silently | explicit error telling the user to rebuild |
| L12 | scheduler unlocked/unwired | in-process lock; wiring is B3 |
| Eval | circular detector/disclosure checks; fakes could not expose M1-M3 | +7 evasion payloads (38 total), summary-templating and RAG-exposure rows, timeout-immutability and naive-datetime tests |


## 7. Runbook

- **First run with oMLX:** start oMLX; `export OMLX_API_KEY=...` if required; `make run`; Chat → Advanced → **Rebuild RAG Index**
  (re-embeds all vault docs with the oMLX embedding model); skills install automatically on first agent run (`make install-skills`).
- **Use the platform:** Chat → agent selector → **Coordinator** → ask e.g. "How do my utility bills this quarter affect my budget,
  and what should I watch on NVDA today?". Review proposals in Agents → **Core Memory**.
- **Add a specialist agent:** write a worker `(PlanStep, inputs) -> list[Finding]` in `workers.py`; register it in
  `service.build_coordinator`; extend `plan_for`; add a template role + skills; add tests and an eval case.
- **Add a data flow between agents:** add a schema to `schemas.py`, register in `EDGES`, send via `MessageBus`, add a
  disclosure test. Never reuse a wider schema.
- **Add a skill:** `skills/<slug>.md` with `name`/`description` frontmatter; attach in `templates._SKILLS`.
- **Add a document category:** see `AGENTS.md` item 4 (`data_extraction/categories.py`).
- **Add an out-of-scope rule:** `policy.RULES` + `MESSAGES` + cases in `evals/refusal.py` (both refuse and allow lists).

## 8. Decisions log

- D1 Rule-based planner (auditable, testable) instead of LLM planning; LLM planning can be layered later behind the same `Plan` validation.
- D2 Merge/escalation are deterministic code, not LLM judgement; the LLM only interprets inside workers.
- D3 Untrusted data safety rests on schema validation, not on the extractor model obeying instructions.
- D4 Thread pool + `asyncio.run` per worker (Streamlit is sync); vault access from worker threads must stay read-mostly.
- D5 Scheduler runs on app open (no daemon); a cron/launchd caller can invoke `schedule.run_due` headlessly.
- D6 Ollama kept but disabled (one flag) rather than deleted, to avoid a risky removal across ~20 files.
- D8 RAG architecture: **Advanced RAG inside the document retriever** (query analysis to filters, hybrid retrieval, rerank, context assembly, numeric grounding check); **Modular behaviour only at system level** (routing between structured records / semantic search / public evidence, Coordinator orchestration, deterministic fusion, approval-gated memory). No free-running retrieve-reflect loops (latency, injection surface, hard to evaluate); at most one bounded re-retrieve when the grounding check fails. See `docs/rag_design.md` §8.1.
- D7 Latency target applied to the 5-step fan-out workload; B1 target to the cross-domain subset (see §5 notes).

## 9. Feedback traceability

See `docs/capstone_feedback_response.md` (Week 1 + Week 2 table mapping each point to code and evals).
