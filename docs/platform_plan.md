# Agent Platform: rules, architecture, contracts and measured results

**Audience: a coding agent (or human) with no prior context.** This file is the reference (what exists and the rules it obeys).
What is left to build, card by card: **[roadmap.md](roadmap.md)**. Overview and diagrams: [architecture.md](architecture.md).
Index: [README.md](README.md).

## 0. How to work on this (read this first)

1. Read `AGENTS.md`, then this file §2 (invariants) and the §3.1 row for the component you touch.
2. Setup/run: `make install`; tests `make test`; evals `make eval` (must print `Overall: PASS`); app `make run`.
   Tests that need a venv: `PYTHONPATH=. venv/bin/pytest tests -q`. Baseline: **all green** (the 3 former `test_chroma_store.py` failures were fixed by R0/B10). Anything failing is yours.
3. Definition of done for any change: code + tests; `make test` and `make eval` green; no new `pass`/stub bodies;
   [roadmap.md](roadmap.md) §1 status and `docs/security_design.md` changelog updated in the same change; no real personal data in tests/fixtures
   (synthetic only); never print `env`, `.env` or vault contents.
4. Never weaken an invariant in §2 to make a test pass. If one seems wrong, stop and ask the user.
5. Do not commit or push unless the user asks. Branch from `main` per roadmap card; never commit to `main`.

## 1. Goal and scope

personal_ai is a local, security-first multi-agent **Personal Assistant + Forecaster** (capstone). One Streamlit app
(`src/interface/`), one encrypted vault, local inference on **oMLX** (Ollama disabled by default). A **Coordinator** delegates
to specialists (expense/records, market, forecaster with parallel sub-specialists, verifier), merges findings
deterministically, escalates uncertainty to the user, and never lets untrusted content or an agent change memory/act.

In scope: records Q&A, bill/cash-flow analysis, evidence-based forecast research, daily briefs, read-only Gmail digest,
scheduled runs. **Out of scope (refused in code):** trading, payments, shell commands, medical diagnosis, revealing secrets.

stock_predictor (sibling repo `../stock_predictor`, FastAPI) is fully absorbed as pure services; it is not
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
| S17 | Workflow-slot agents: tools, output schema and budget are fixed by the slot; user tweaks touch only `EDITABLE_FIELDS`; the shared safety rules stay last; slot runs log ids/status only | `agents/slots.py`, `store.builtin_agent`, `templates.compose_instructions` | `test_agent_overrides.py`, `test_slots_explain.py`, `test_agents_page_smoke.py` |
| S12 | Skills are instructions only (no personal data); installer never overwrites user edits | `orchestration/skill_pack.py` | `test_orchestration_service.py` |
| S18 | Approved Core Memory is read only via `memory.recall` on the calling thread; recalled facts go only to the report's "From your approved memory" section and (trusted cash-flow facts) the expense step's `budget_baseline:*`; never on a bus edge, never to market/forecaster/email/portal inputs, never into RAG; untrusted-approved facts stay labelled; Forget deletes behind a purpose-bound (`forget`) single-use UI token | `memory.py`, `service.run_query`, `coordinator.dependency_inputs` | `test_core_memory_recall.py`, `test_composition_leaks.py`, eval P4/P5 rows |
| S19 | Dependency findings reach a dependent worker only through a per-edge allowlist (`coordinator.DEPENDENCY_INPUTS`: expense→market `cashflow_delta:*`, brokerage→market `holdings`; everything else nothing) | `coordinator.py` | `test_composition_leaks.py` (positive control), eval P4 row |
| S20 | Robinhood: code-built calls only (fixed tool, validated ticker/id/date args, never model-built); exact-name allowlists + `ROBINHOOD_NEVER` denylist (banking, credit, orders, `decline_*`, `set_*`); no writes; broker free text dropped; account data only in Portfolio fit + a tool-less local agent, never in the AI-review snapshot, logs or any request except account tools | `forecaster/robinhood.py`, `mcp.SENSITIVE_TOOL_PREFIXES` | `test_robinhood.py`, `evals/robinhood.py` |
| S21 | Trader (Track T): output has no order/ticket shape and no write tool is reachable; upstream `trader-broker` not ported | `trader/` schemas (`extra="forbid"`), Robinhood allowlists | `evals/trader.py` shape row, `test_trader_agent.py` |
| S22 | Trader sizing/risk use only user-entered limits (`TRADER_*`); broker account figures never enter trader inputs/outputs | `trader/sizing.py`, `trader/risk.py` | `evals/trader.py` shape row (no broker/MCP import in `src/trader`) |
| S23 | Trader Monte Carlo is seeded (deterministic), bounded by `TRADER_*` caps, labelled a simulation | `trader/timemachine.py` | `evals/trader.py` simulation row, `test_trader_engines.py` |
| S24 | Trader paper agents: prompts are code-built relative numbers from bars up to the signal (no ticker/date/price, no lookahead); replies are closed-choice `TraderDecision` (extra fields rejected); code alone sets entry, size and risk; ledger is vault-only, never sent to a broker | `trader/agents.py`, `trader/strategies.py`, `trader/paper.py` | `test_trader_paper.py`, `evals/trader.py` agent-safety + lookahead rows |
| S25 | Robinhood Portfolio chat: code reads holdings (account tools only) and computes figures; a tool-less model on the loopback endpoint answers; no account numbers in the prompt; `policy.screen` before any Robinhood call; activity log holds status + timing only | `agents/portfolio_chat.py`, `interface/main.py::_respond_portfolio` | `test_portfolio_chat.py`, eval S25 row |

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
| `src/agents/orchestration/schemas.py` | Typed contracts | `Finding`, `Plan`, `PlanStep(symbol?)`, `CashflowDelta`, `SymbolRequest`, `TraderRequest`, `HoldingsSummary`, `EmailFacts`, `StatementFacts`, `EDGES`, `SAFE_TEXT` |
| `.../bus.py` | Scoped message bus | `MessageBus(vault, edges, canaries).send(sender, receiver, payload)`; `DisclosureViolation` |
| `.../quarantine.py` | Untrusted-input extractor | `extract_facts(raw, source, schema, client) -> Extraction`; `sanitize_text` |
| `.../memory.py` | Core Memory gate | `propose`, `list_pending`, `list_core`, `issue_approval_token`, `approve(token, acknowledge_untrusted)`, `reject`; `ApprovalError` |
| `.../coordinator.py` | Delegate/merge/escalate | `Coordinator(workers, max_workers, max_retries, deadline_s, max_age, transient, recheck).run(plan) -> RunReport`; `merge`, `validate_plan`, `escalations_for`, `requires_confirmation` |
| `.../workers.py` | Service adapters | `plan_for(query)`, `expense_worker(get_records)`, `forecaster_worker(research, swarm)`, `trader_worker(providers)`, `market_worker(providers, bus)`, `render_report(report)` |
| `.../ingest.py` | Gmail/portal ingestion | `ReadOnlyMcp`, `gmail_fetcher`, `digest_emails`, `ingest_statements`, `email_worker`, `portal_worker`, `PortalFetcher` protocol |
| `.../schedule.py` | Scheduled runs (vault) | `add_job`, `list_jobs`, `remove_job`, `due_jobs`, `run_due(vault, run_query)` |
| `.../policy.py` | Refusals | `screen(query) -> Refusal or None`, `RequestRefused` |
| `.../templates.py` | Built-in agent configs | `BUILTIN_AGENTS` (`builtin-<role>`: coordinator, expense, health, email, portal, market, forecaster, verifier), `list_builtin()` |
| `.../skill_pack.py` + `skills/*.md` | Built-in skills | `install_builtin_skills()` (11 skills, incl. 4 `trader-*` adapted from day-trader-skill) |
| `.../service.py` | UI wiring | `build_coordinator(vault, get_records, scenario_factory, extra_workers)`, `run_query` (applies policy), `propose_from_report` |
| `src/agents/{runtime,mcp,store,skills}.py` | v1 agent loop, MCP, vault records | see `multi_agent_design.md` |
| `src/forecaster/*.py` | Public-data research | `research`, `scenarios`, `outcomes`, `technical`, `recommend`, `screens`, `news`, `report`, `health`, `providers` |
| `src/agents/slots.py` | Workflow slots: fixed default agent per AI step, tool clamp, skills folded in | `SLOTS`, `agent_for(vault, slot_id)`, `critic_agents(vault)`, `log_run` |
| `src/forecaster/explain.py` | Thesis / watchlist explainer agents (closed schema, grounded numbers) | `explain_thesis`, `explain_leans` |
| `src/forecaster/tot.py` | Tree-of-Thought thesis search (Week 4) | `search(snapshot, critique) -> ThoughtTree`; `Thought`, `Node`; design + rationale in [forecaster.md](forecaster.md) Part 2 |
| `src/trader/*.py` | Trader (Track T, [trader.md](trader.md)): plan + AI paper agents | `pipeline.analyze`, `strategies.{orb,daily}_signals`, `agents.decide/review`, `paper.execute/load/save`, `performance.summarize/equity_curve/monthly`, `runner.run` |
| `src/ai_engine/engine.py` | Client factory | `build_chat_client`, `build_embed_client`, `client_kwargs_from_config`, `EngineDisabledError` |
| `src/ai_engine/omlx_client.py` | oMLX client | OpenAI-shape chat/embeddings, tool-call mapping (`_to_openai_messages`, `_from_openai_message`) |
| `src/interface/` | UI | `main.py` (Coordinator in chat selector via `_respond_coordinated`), `forecast.py` + `forecast_tabs.py`, `trader_tab.py` + `trader_paper_tab.py` (Forecast → Trader), `agents.py` (Core Memory tab) |
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
`coordinator→market (HoldingsSummary)`, `coordinator→trader (TraderRequest)`, `email→coordinator (EmailFacts)`, `portal→expense (StatementFacts)`.
Claim keys added by Track T: `trader:<SYM>:{entry,plan,odds}`.

### 3.4 Agent roster

All are code-constant templates (not editable); the user binds MCP servers in Agents → MCP Servers. Roles/tools/skills are in
`templates.py` (`_ROLES`, `_SKILLS`); every prompt carries the shared safety rules (`_COMMON`). Chat-selectable built-ins: Coordinator, Expense & Records, Market, Forecaster (via the coordinator) and **Robinhood Portfolio**
(`builtin-portfolio`, S25: code-read holdings, local tool-less model). Skills attached: personal-finance-analysis,
market-research-brief, untrusted-content-handling, conflict-escalation, forecast-evaluation, data-minimization, forecast-critic.
Forecast workflow agents (tool-less slots, tweakable in Agents → Forecast workflow): 4 critics, thesis/watchlist explainers, news
analyst, portfolio fit, and the Trader paper agents `builtin-trader-{orb,breakout,bounce,reviewer}` (skills `trader-orb-method`,
`trader-swing-method`, `trader-risk-rules`, `trader-review`; numbers-only prompts, closed-choice replies, S24).

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
| `PERSONAL_AI_INFERENCE_CONCURRENCY` | `2` | App-wide cap on in-flight chat requests to the local server (all agents share it); see `ai_engine.md` "Memory budget" |
| `PERSONAL_AI_FORECAST_CRITIC_MODEL` | `Qwen3-4B-Instruct-2507-MLX-8bit` | default model of the 4 AI-review critic agents (oMLX id = model dir name); `""` = worker/chat model; per-agent tweak wins |
| `PERSONAL_AI_OMLX_WORKER_MODEL` | unset (= chat model) | Optional smaller model for background roles: forecast specialists/ToT critics, quarantine extractor, verifier, planner |
| `PERSONAL_AI_SKILLS_DIR` | `~/.personal_ai/skills` | skills install dir |
| `PERSONAL_AI_FRED_API_KEY`, `..._ALPHA_VANTAGE_API_KEY`, `..._SEC_USER_AGENT` (or unprefixed `FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `SEC_USER_AGENT`) | unset | optional forecaster sources; `.env` is loaded by the app and by `make state-report` |

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
| News analyst: injected text reaching output (38 headlines × 5 extractor behaviours, F7) | 0 | 0/190 | closed `NewsFacts`; feed-unlinked tickers ignored |
| Quarantine text reaching a finding summary | 0 | 0 / 38 | summaries are templated |
| Core Memory/schedule/audit keys exposed to RAG | 0 | 0 / 6 | |
| Core Memory commits without approval | 0 | 0 / 195 | |
| Minimal disclosure (canary/PII in bus, log, egress) | 0 | 0 | |
| Refusal of out-of-scope asks / false refusals | 100% / 0 | 24/24 / 0/12 | regex first gate; real controls are allowlists |
| ToT thesis selection vs linear / critic-vote baselines (8 labeled synthetic cases) | ≥ both, 100% | 8/8 vs 6/8 / 7/8 (2026-11-02) | ablation of the search policy, not market accuracy |
| ToT bounded search: max nodes / critic reviews | ≤ 9 / ≤ 1 per case | 7 / 3 of 8 (5 converged with 0 LLM calls) | |
| ToT held-out accuracy (seeds 200-299; tuned on 0-99) vs linear / critic-vote (P8) | ≥ both | 0.76 vs 0.59 / 0.70 (abstain 0.02) | planted-label generator `evals/tot_synth.py`; 100-199 burned |
| ToT held-out Brier, dev-calibrated (P8) | < both | 0.414 vs 0.488 / 0.430 | raw normalised scores: 0.473 vs 0.495 / **0.378** (vote better uncalibrated) |
| Stress set: action + figures correct, coordinated / B1 (P1) | 100% / info | 56/56 / 34/56 | 6 kinds incl. ambiguous, incomplete, stale, conflict, wrong-record |
| Planner chose the expected specialists (golden + stress, P1) | ≥ 90% | 100% (76/76) | |
| Evidence relevance: wrong-record citations caught (P2) | 100% | 7/7 | W6 target ≥ 95% |
| Escalation precision / recall (P6) | ≥ 90% / ≥ 90% | 100% / 100% (tp 31, fp 0, fn 0) | escalations per run mean 1.09, max 4 (info) |
| Missing statements reported, never invented (P12) | 100% | 3/3 | needs a ledger expectation |
| Core Memory recall / superseded-or-forgotten returned (P5) | ≥ 9/10 / 0 | 9/9 / 0 | |
| Composed leakage: canaries outside allowed sinks (P4) | 0 | 0 (114 runs, 969 sinks, 102 hops) | golden + stress + service chains with memory |
| Robinhood: broker free text / account canaries / valuation golden (Track R) | 0 / 0 / 100% | 0/5 / 0 in 19 sinks × 3 / 3/3 | synthetic fixtures; no live account |
| Robinhood Portfolio chat: account number in prompt/log, trade asks reaching Robinhood (S25) | 0 | 0 in 5 | synthetic account fixtures |
| Trader: plan shape / seeded Monte Carlo / risk golden (Track T) | 0 / 100% / 100% | 0 in 13 checks / 4/4 / 5/5 | synthetic bars; advice only |
| Trader paper agents: hostile replies accepted / lookahead / ledger golden (Track T) | 0 / 0 / 100% | 0 in 90 / 0 in 68 / 7/7 | synthetic bars + fake clients; live model results = USER |
| Stability: every hard row across `RUNS=10` perturbed runs (P7) | 100% | 10/10 each | shuffle, duplicates, paraphrase, jitter, workers 1/2, concurrency 1 |

**How success is scored.** A golden task passes only if *every* expected claim is present with the expected value (±0.01) or the
expected status (`evals/golden.py::score`); partial credit does not exist. The 30 tasks are hand-built synthetic cases: 10 cross-domain
(records + market), 20 single-domain. Fault, conflict, injection, disclosure and refusal rows are separate suites with their own
pass rules (`evals/run.py::evaluate`). The stress set (P1), relevance (P2), escalation (P6), composed-leakage (P4), memory-recall (P5), stability (P7),
held-out ToT (P8) and Robinhood (Track R) rows are measured as of 2026-11-21 (rows above). All data is synthetic.

Answer to "benefit of coordination vs running each repo alone": cross-domain tasks (records + market) are unanswerable by B1
(0%) and about 2.4x faster than sequential B2 on the fan-out workload, without regressing single-domain tasks.

## 6. Status

Open work lives in [roadmap.md](roadmap.md) (status table §1, cards §4). History of what was built and why: `CHANGELOG.md`.
Completed phases (all accepted by `make test` + `make eval`; nothing verified on live oMLX yet): 0 baseline, 1 oMLX-only engine,
2 stock_predictor port, 3 UI merge, 4 agent infra, 5 agent configs/skills/policy, 6 untrusted ingestion (fixtures), 7 evaluation,
8 docs, 9 independent audit (now in `security_design.md` "Independent audit"), 10 PR #17 hardening merge, 11 Week 4 ToT.
Former backlog cards B1-B19 and RAG cards R0-R11 are DONE except those carried into the roadmap (B1/B6/B19 → P9, B17/B18/R10 →
roadmap §6). Track F, P1-P8, P12 and Track R done (2026-11-21, PR #30); Track T Trader T0-T5 done (PRs #31-#32). Test count at last full run: 905 pass, 0 fail (2026-11-21).

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
- D9 Tree of Thought only in the forecaster (beam width 3, depth 2, scenario-diverse beam, deterministic thought generator, LLM specialists as a lazy shared critic). Records, planning and RAG stay linear by design. See [forecaster.md](forecaster.md) Part 2.
- D10 CrewAI/LangChain/MCP are mapped to ToT roles, not imported: no second orchestration framework, all inference stays on the loopback client, invariants stay in this repo's code.
- D13 Framework review (2026-11-20). Only **MCP** is used (official `mcp` SDK, read-only). No weekly submission commits to LangChain,
  LangGraph, LangSmith or CrewAI (W4 maps roles and says "plain Python rather than importing"; W5 maps `Plan` onto LangGraph nodes/edges).
  Verdict for this system: **LangSmith no** (cloud tracing ships vault-derived prompts off-machine; use the encrypted activity log or a
  self-hosted tracer); **LangChain no** (one local OpenAI-shape server, custom measured RAG; adds hidden prompts and default clients);
  **CrewAI no** (free agent chat is what per-edge schemas prevent); **LangGraph not now**: its interrupts/checkpoints fit P3/P6/memory
  approval, but default checkpointers are plaintext SQLite (needs an encrypted vault checkpointer) and a rewrite re-proves every eval row.
  **Revisit when:** runs must pause and resume across app restarts, plans become dynamic/looping beyond the DAG, or the course requires
  hands-on framework use (then do the roadmap §6 LangGraph parity eval first).
- D11 Step budgets come from code: forecaster steps get `FORECAST_STEP_TIMEOUT_SECONDS` (research + specialist deadlines + 5 s); the LLM planner cannot set `timeout_s`.
- D12 Hardware budget (one 128 GB laptop): all agents share one local server. Memory scales with resident models and with in-flight requests, not with the number of agents, so concurrency is capped app-wide (`PERSONAL_AI_INFERENCE_CONCURRENCY=2`) and background roles can use a smaller `PERSONAL_AI_OMLX_WORKER_MODEL`. Speed comes from smaller worker models, not more parallelism.
- D14 Trader paper agents (Track T5): a **local paper ledger**, not Alpaca paper, so no write tool enters the platform (S21); agents
  see **anonymised, no-lookahead numbers** (no ticker/date/price) so a backtest can't be won from the model's memory of history;
  every agent is scored against a **take-every-signal baseline** on the same signals; fixed per-trade budget keeps results independent
  of run order; decisions are cached so the per-run call budget extends coverage instead of re-asking.
- D7 Latency target applied to the 5-step fan-out workload; B1 target to the cross-domain subset (see §5 notes).

## 9. Feedback traceability

Every roadmap card names the weekly feedback it answers ([roadmap.md](roadmap.md) §4).
