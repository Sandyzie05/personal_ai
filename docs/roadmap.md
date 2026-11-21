# Roadmap: what is built, what is left, and how to build it

**Audience: a coding agent (or human) with no prior context.** This is the only place remaining work is planned.
Rules and invariants: [platform_plan.md](platform_plan.md) §0-§2 and `AGENTS.md`. Every card below is self-contained:
do them in order, one card per branch/PR, and a card is done only when its **Accept** commands pass.

Course commitments come from the weekly checkpoints and instructor feedback (Weeks 1-6, kept outside the repo in `.idea/`).
Each card names the feedback it answers (`W5` = Week 5 feedback).

## 00. Start here (handoff for the next session, updated 2026-11-21)

**State.** `main` contains everything through PR #25 (Track F: Forecast overhaul on configurable workflow agents). No open PRs.
`make test` 772 pass, `make eval` Overall PASS (offline, synthetic). Two laptops: this repo is developed on one without models;
the app runs with real data and local models (oMLX, Qwen3.8-27B) on the other. **Nothing has been verified on live models yet.**
AI-review critics now default to `Qwen3-4B-Instruct-2507-MLX-8bit` (`PERSONAL_AI_FORECAST_CRITIC_MODEL`).

**Waiting on the user (model laptop), in this order:**
0. Download `lmstudio-community/Qwen3-4B-Instruct-2507-MLX-8bit` into oMLX; confirm its id in `curl 127.0.0.1:8000/v1/models`
   (if it differs, `export PERSONAL_AI_FORECAST_CRITIC_MODEL=<id>`). A saved critic tweak with a model keeps that model.
1. `git pull` → `venv/bin/pip install -U -r requirements.txt` → `venv/bin/pip uninstall -y fastapi` (stray, see
   `user_testing_guide.md` §0 step 6) → `venv/bin/pip check`.
2. Restart `make run`; Forecast → AAPL → *Research ticker* → *Run AI review*. Optionally tweak a critic (Agents → Forecast
   workflow) and run again.
3. Paste a state report (opencode prompt below). The `<LIVE>` section must list critics completed N/4 with each status,
   `invalid_reason` and seconds.

**Next card for the agent, decided by that report:**

| Report says | Do |
|---|---|
| ≥ 3/4 critics `completed` | Mark F1 live ✓ (§0 diagram + Miro), then **F7 remainder** (LLM news analyst), then **P1** |
| `timeout` on 2+ critics | Check critics actually run on the 4B (agent chip); else `PERSONAL_AI_FORECAST_SPECIALIST_CALL_SECONDS`; never drop the deadline |
| `invalid (truncated)` | Raise the critic agent's max tokens in the Agents tab, up to `FORECAST_MODEL_MAX_TOKENS` (700; ForecastSettings allows ≤ 1024) |
| `invalid (not_json)` / `(schema)` | Check `omlx_client` honours `format=` (json_schema response_format) on the live server; add a unit test with the observed shape (no model text in fixtures) |
| `invalid (unknown_evidence)` | Model invents ids: add the evidence-id list explicitly to `SLOT_RULES` user message; keep the validation |
| oMLX `no (...)` | Fix connectivity first (`state_report.md` §2 explains each error) |

**Opencode prompt for the model laptop** (the user pastes it after each testing session; output is pasted back here, never
committed): run `make state-report` per `docs/state_report.md`; ask the user once for the user-testing rows, live timings
(including the AI review critic table), UI document counts, errors (type + file:line); fill only the placeholders; re-read
against §1 of that doc; print one markdown block.

**How work is done here (learned this session).** One branch + PR per card unless the user asks to batch. If a PR conflicts,
merge `main` into the branch (force-push is blocked by a local hook; never rebase a pushed branch). After each card: update §0
diagram, §1 status, the Miro frames named in §0, `CHANGELOG.md`, and `security_design.md` when security behaviour changed.
The repo is **public**: no personal paths, names, amounts or vault data in docs, tests or fixtures.

## 0. Where we are (progress journey)

Updated after every card (also mirrored on the Miro board, frame "Progress journey"). Green = done, amber = in progress, grey = planned, blue = needs a live run by the user.

```mermaid
flowchart LR
  W1[W1 proposal + assessment] --> W2[W2 reasoning, memory, tools] --> W3[W3 RAG R0-R6] --> W4[W4 ToT forecaster] --> W5[W5 multi-agent] --> W6[W6 safety plan] --> D[Docs + roadmap<br/>2026-11-20]
  D --> FX[Fixes: Yahoo UA, .env keys, NumPy 2, state report] --> F[Track F: Forecast overhaul on configurable agents F1-F10] --> FL[F1 live check + F7 news analyst] --> P1[P1 stress set] --> P2[P2 verifier relevance] --> P3[P3 clarify] --> P12[P12 missing statements] --> P5[P5 memory recall] --> P4[P4 composed leakage] --> P6[P6 escalation metrics] --> P7[P7 stability] --> P8[P8 held-out ToT]
  P8 --> P9[P9 live run] & P10[P10 RAG calibration] --> P11[P11 final submission]
  classDef done fill:#2e7d32,color:#fff,stroke:#1b5e20
  classDef now fill:#f9a825,color:#000,stroke:#f57f17
  classDef todo fill:#eeeeee,color:#333,stroke:#9e9e9e
  classDef user fill:#1565c0,color:#fff,stroke:#0d47a1
  class W1,W2,W3,W4,W5,W6 done
  class D,FX,F done
  class FL now
  class P1,P2,P3,P12,P5,P4,P6,P7,P8,P11 todo
  class P9,P10 user
```

**Rule for whoever finishes a card:** in the same change, move it to `done` (and the next card to `now`) here, update §1,
and update the Miro board https://miro.com/app/board/uXjVEVwdVZo=/ : frame "0 Progress journey" (Mermaid diagram widget
`3458764691150425568`: change the node's class `todo` → `done`/`now` and the "Last update" text) and frame "6 Roadmap"
(recolour the card shape green `#B3E65F`). Frames 1-4 hold the system flow, phases, success criteria and RAG; frame 5 the user flow.

## 1. Capability status (implemented vs planned)

Status: **DONE** = code + tests + eval row · **PARTIAL** · **PLANNED** (card below) · **USER** = needs a live run on the user's laptop.

| Capability | Status | Evidence / card |
|---|---|---|
| Encrypted vault, single key path, localhost-only UI | DONE | `security_design.md`, `test_auth.py`, `test_vault_lock.py` |
| Upload → classify → extract → structured records (category-driven) | DONE | `data_extraction/categories.py`, `test_extractor.py` |
| Advanced RAG: header chunks, period filter, hybrid BM25+dense, dedupe, abstention, grounding check | DONE offline | `rag_design.md`, `make eval-rag` (recall@5 1.0) |
| RAG thresholds (`min_relevance`, `k`) tuned on the real embedder | USER | P10 |
| Coordinator: typed plan, parallel waves, bounded retry, deterministic merge, escalation | DONE | `coordinator.py`, `make eval` (63/63 faults, 26/26 conflicts) |
| Policy refusals (trade, pay, shell, diagnose, secrets) | DONE | `policy.py`, eval 24/24, 0/12 false refusals |
| Quarantine for untrusted email/portal/news text | DONE | `quarantine.py`, eval 0/38 reaching summaries |
| Per-edge bus schemas + PII/canary scan | DONE per edge | `bus.py`, eval 0 leaks / 190 msgs |
| Leakage across multiple hops and through shared memory | PLANNED | P4 (W6) |
| Core Memory: propose → UI-token approval → supersede | DONE | `memory.py`, eval 0/114 unauthorized |
| Core Memory **read path** (approved facts used in later answers) | PLANNED | P5 (W1 assessment, W2) |
| Verifier: figure appears in cited evidence | DONE | `verifier.py` |
| Verifier: cited record matches claim category/period/account | PLANNED | P2 (W5, W6) |
| Ambiguity → one clarifying question | PLANNED | P3 (W2, W6) |
| Missing statement observed → told + offered a sync (observation drives the next step) | PLANNED | P12 (W1, W2) |
| Single-source / partial answers carry a caveat | PARTIAL: partial quarter yes, single source no | P2 step 5 (W6) |
| Planner picks the expected specialists (tool-selection metric) | PARTIAL: implied by golden claims, not measured | P1 step 6 (W1 assessment) |
| Scheduled runs (e.g. a daily brief query) | DONE | Agents → Scheduled; `schedule.py` |
| Labelled stress set with expected action | PLANNED | P1 (W5, W6) |
| Escalation precision/recall + reviewer decision log | PLANNED | P6 (W6) |
| Repeat-run / perturbed-condition stability | PLANNED | P7 (W6) |
| Forecaster: research, 4 parallel critic agents, ToT verdict, explainer, outcome tracking | DONE offline (Track F); live critic completion USER | F1 live check in the next state report |
| Built-in agents tweakable in the Agents tab; every AI step has an assigned agent + chip | DONE | F2, F3 (`test_agent_overrides.py`, `test_agents_page_smoke.py`) |
| Chat selector lists every agent in the system | DONE | F10 |
| LLM news analyst behind quarantine (Events) | PLANNED | F7 remainder |
| Tree-of-Thought thesis search (beam 3, depth 2, ≤ 9 nodes) | DONE offline | `forecaster.md` Part 2, eval 8/8 |
| ToT held-out evaluation + calibration (Brier) | PLANNED | P8 (W4, W5, W6) |
| Gmail / brokerage read-only workers | DONE vs fakes | eval 7/7; no real MCP run |
| Bill ingestion: validation, manifest, ledger, Gmail attachments, portal recipe runner | DONE vs fixtures | `rag_design.md` R7-R9 |
| Real provider portal recipes | USER | R10 in `rag_design.md` |
| Live oMLX end-to-end (chat, RAG, Coordinator, ToT, `make eval-live`) | USER | P9 |
| Daily Gmail digest *sent* to a messaging app (W2 idea) | DROPPED | sending is a write; read-only invariant S6. Digest is shown in-app |
| Voice, cross-device sync, key rotation/backup, file watcher | OUT OF SCOPE (capstone) | `security_design.md` |

All evaluation data is synthetic or public. No real-market skill is claimed.

### 1.1 Weekly commitment audit (2026-11-20)

Each commitment made in a weekly checkpoint or raised in feedback, checked against the code.

| Week | Commitment | Status | Card |
|---|---|---|---|
| W1 assessment | Allowlisted, schema-validated tools; ≥ 18/20 labelled tasks pick the right tool sequence | Tools DONE; metric not measured | P1 |
| W1 assessment | ≤ 5 orchestration steps, 1 retry, ≥ 9/10 recoverable faults complete, unrecoverable abstain | DONE (63/63) | – |
| W1 assessment | Approved task memory retrieved later (≥ 9/10), corrections supersede, forgotten never retrieved | Supersede DONE; recall + forget missing | P5 |
| W1 assessment | ≤ 3 alternative scenarios with a rubric; contradictory evidence changes the selection | DONE (ToT) | – |
| W1 assessment | Parallel specialists with overlapping timestamps; failed specialists visible | DONE | – |
| W1 assessment | Adversarial tests: injection, invalid args, locked vault, unapproved writes, exfiltration | DONE | – |
| W1 assessment | Forecast eval: chronological split, directional accuracy, calibration, coverage, abstention, benchmark | Directional/coverage/benchmark DONE (`report.py`); calibration missing; realized outcomes need elapsed horizons | P8 |
| W1 | Check which statements exist before summarizing; never invent missing amounts | Partial quarters flagged; missing statements not reported | P12 |
| W2 | Observation drives replanning: a missing statement triggers a fetch or a confirmation | Ledger exists (`pipeline.overdue`), not used by the expense worker | P12 |
| W2 | Expense → Market shares only the cash-flow delta | DONE (bus edge) | – |
| W2 | Daily Gmail digest sent to a messaging platform | DROPPED (write action); in-app digest + scheduled query | – |
| W2 | Approval-gated Core Memory | DONE | – |
| W3 | Chunking fix, headers, month filter, keyword search, answer check, test set first | DONE offline | – |
| W3 | Set the similarity cutoff from data, not a guess | Not tuned on real embedder | P10 |
| W3 | Show both documents when two disagree | DONE (conflict escalation) | – |
| W4 | ToT live run (converged + depth-2 case) | Not run | P9 |
| W4 feedback | Thresholds tested only on 8 self-labelled cases | Open | P8 |
| W4 feedback | CrewAI/LangChain/MCP mapping unclear | DONE (`architecture.md` §5) | – |
| W4 | ThoughtTree as a read-only MCP resource | Not built (optional) | §6 |
| W5 feedback | Explain how the 100% is measured | DONE (`platform_plan.md` §5) | – |
| W5 feedback | Ambiguous / incomplete / stale / conflicting stress tests | Open | P1, P3 |
| W5 feedback | Verifier must check accuracy and relevance | Open | P2 |
| W5 feedback | Forecast quality vs single-agent baseline | Open | P8, P1 (B1 on stress set) |
| W6 | Evidence relevance ≥ 95%, escalation precision/recall ≥ 90%, Brier on held-out | Open | P2, P6, P8 |
| W6 | Single-source answers proceed with a caveat | Open | P2 |
| W6 | Reviewer accept / correct / dismiss, logged; overrides become test cases | Open | P6 |
| W6 feedback | Safety results consistent across repeated tests and changing conditions | Open | P7 |
| W6 feedback | No unintended flow between agents or into shared memory | Open | P4 (+ P5 rule S18) |

## 2. Order

Track F (user decision 2026-11-21) is merged except the F1 live check and the F7 news analyst. **Remaining order:**
F1 live check (user) → F7 remainder → `P1 → P2 → P3 → P12 → P5 → P4 → P6 → P7 → P8` (code, offline, agent-doable) →
`P9, P10` (user runs live) → `P11` (final packaging).
P1 comes first because P2, P3 and P6 add their cases to it. P5 comes before P4 so the leakage eval also covers memory reads.

## 3. Conventions for every card

- Branch from `main`; never commit to `main`. Commit/push only when the user asks.
- Synthetic data only. No real names, accounts or amounts in fixtures.
- New behaviour = unit test in `tests/` + an eval row in `evals/run.py::evaluate` (`hard=True` unless the card says info).
- Never loosen an existing gate, schema or threshold to make a case pass. If a new case fails, fix the code or record it as a finding.
- Update the §0 progress diagram and the Miro "Progress journey" frame.
- Finish with: `make test` green, `make eval` → `Overall: PASS`, then update §1 above, `platform_plan.md` §5 (metrics) and
  the `security_design.md` changelog when security behaviour changed, and add a `CHANGELOG.md` entry.

## 4. Cards

### Track F. Forecast overhaul on configurable agents (do first)

**Problem (model-laptop test, 2026-11-21).** Research output is a wall of raw numbers and JSON; specialist review failed
(2 `invalid`, 2 `timeout`, 0 coverage) with no reason shown; Recommendations, Drilldown, System and Screens crashed (NumPy 1.26 vs
pyarrow, fixed in PR #23); Events broken; Validation unexplained. Built-in agents are code constants (`store.update_agent` is a
no-op for them) and the forecast specialists do not use the Agents system at all (prompts hardcoded in `forecaster/scenarios.py`).

**Target design (user decisions).**
- Every AI step is a **workflow slot** with a **fixed default agent**. The user **tweaks that agent in the Agents tab** (model,
  temperature, max tokens, extra instructions, skills, MCP servers/tools within the slot's allowlist) and can **reset to default**.
- Locked by the slot in code, never editable: tool allowlist, output schema, time/token/call budget, the shared safety rules
  (`templates._COMMON`), untrusted-input rules (S1, S16). Prompts are defence in depth; schemas and budgets are the control.
- Every feature that runs an agent shows a chip: **"Agent: <name> · <model> · Tweak in Agents"**, plus per-run status and time.
- The Chat agent selector lists **every agent in the system** (General, all built-ins that are chat-capable, all custom agents).

Slots in scope (Forecast first; other pages later):

| Slot | Default agent | Tools | Output |
|---|---|---|---|
| `forecast.critic.macro` / `.valuation` / `.event` / `.downside` | `builtin-critic-<role>` | none | `SpecialistOpinion` (evidence ids from the snapshot) |
| `forecast.thesis_explainer` | `builtin-thesis-explainer` | none | `ThesisExplanation` (closed fields + ≤ 3 sentences citing evidence ids) |
| `forecast.news_analyst` | `builtin-news-analyst` | none; input = quarantined headline facts only | closed-enum sentiment counts per topic |
| `forecast.watchlist_explainer` | `builtin-watchlist-explainer` | none | per-symbol closed reason codes + ≤ 2 sentences |

#### F1. Specialist reliability on one laptop — DONE offline 2026-11-21 (live: USER)

- **Now:** `scenarios.ScenarioSwarmService` runs 4 calls under one 60 s deadline (`FORECAST_SCENARIO_DEADLINE_SECONDS`) while
  `PERSONAL_AI_INFERENCE_CONCURRENCY=2`, so 2 calls queue and time out on a 27B model. `max_tokens` is capped at 400
  (`FORECAST_MODEL_MAX_TOKENS`); any non-JSON, truncated or wrongly cited answer becomes `invalid` with no reason.
- **Build:**
  1. Record a closed `invalid_reason` per specialist: `not_json | truncated | schema | unknown_evidence | source_mismatch |
     empty` (no model text). Show it in the UI and the activity log.
  2. Strip a leading `<think>…</think>` block before JSON parsing; request JSON via the existing `format=` schema.
  3. Queue-aware budget: per-call deadline = remaining run budget, and the run deadline =
     `ceil(4 / concurrency) × FORECAST_SPECIALIST_CALL_SECONDS` (new config, default 45). Default the critics to
     `PERSONAL_AI_OMLX_WORKER_MODEL` when set. Raise `FORECAST_MODEL_MAX_TOKENS` to 700 for critics.
  4. `make state-report` adds: last run's specialist statuses + reasons + latency (counts/enums only). *As built:* the script
     cannot unlock the vault, so the user copies the AI review table's status/reason/seconds into the report's `<LIVE>` section.
- **Accept:** unit tests per `invalid_reason`; fake-client test with concurrency 2 and 4 critics completes in budget;
  live (user): ≥ 3/4 critics `completed` on AAPL, recorded in the next state report.

#### F2. Editable built-in agents (overrides + reset) — DONE

- **Build:** vault key `agent_overrides` (`{agent_id: {field: value}}`), added to `INTERNAL_VAULT_KEYS` (S13). `store.get_agent`
  merges template + override; `store.update_agent` on a `builtin-*` id writes only allowlisted fields
  (`model, temperature, max_tokens, extra_instructions, skills, mcp_servers, enabled_tools`); `store.reset_agent(id)` deletes the
  override. Instructions sent = slot rules + template rules + `extra_instructions` + `_COMMON` (order fixed; `_COMMON` last).
  Agents tab: built-ins get an Edit form (same form as custom agents, locked fields shown read-only) and "Reset to default".
- **Accept:** tests: override persists across restart; locked fields ignored; reset restores template; `_COMMON` always present;
  AppTest edits a built-in and resets it. `make eval` unchanged.

#### F3. Workflow slot registry + agent chip — DONE

- **Build:** `src/agents/slots.py`: `SLOTS = {slot_id: Slot(default_agent, allowed_tools, output_schema, budget, chat_capable)}`;
  `agent_for(vault, slot_id)` returns the (merged) default agent; tools outside `allowed_tools` are dropped at call time, not just
  hidden. `interface/components.py::agent_chip(slot_id)` renders name, model, status, "Tweak in Agents" link (sets the Agents tab
  selection). Activity log entries gain `slot_id`, `agent_id`, `latency_ms`, `status` (no content).
- **Accept:** tests: slot tool allowlist enforced even if the agent lists more tools; chip renders in AppTest.

#### F4. Dedicated critic agents wired to slots — DONE (Forecast page and Coordinator)

- **Build:** add `builtin-critic-{macro,valuation,event,downside}` to `templates._ROLES` with role prompts moved from
  `scenarios.PROMPTS` (single source), skills `skills/critic-<role>.md` (what evidence counts, when to say uncertain, citation
  rules), no tools. `ScenarioSwarmService` takes `agent_for(vault, "forecast.critic.<role>")` per role: model, temperature,
  max_tokens, instructions. ToT (`tot.py`) is unchanged; it still calls the swarm once at depth 2.
- **Accept:** tests: tweaking the macro critic's temperature/model reaches its client; prompts come from templates only
  (grep test: no role prompt left in `scenarios.py`); `make eval` ToT rows unchanged.

#### F5. Research section redesign (one flow) — DONE

- **Flow:** ticker + horizon → **Evidence** (price chart with SMA 20/50 and Bollinger, plotly; indicator cards; provider chips with
  status; "missing inputs" list) → **AI review** (one row per critic: agent chip, status, reason, latency, stance + confidence,
  cited evidence ids clickable to the evidence card) → **Verdict card** (ToT selected scenario, margin, runner-up, why in the
  explainer's ≤ 3 sentences, "what would change this", uncertainty flags) → **Track** (save forecast; session-sequence input
  moved into an "Advanced: outcome tracking" expander with a plain explanation).
- Raw JSON (provenance, scenario report) only in a "Details" expander. Empty, loading and error states for every block.
- **Accept:** AppTest with fake providers + fake critics: each block renders; failure of any provider or critic shows a reason, not a
  traceback; no `st.json` outside "Details".

#### F6. Watchlist (was Recommendations) — DONE

- Table with lean, confidence, risk, a sparkline per symbol, closed reason codes; `forecast.watchlist_explainer` adds ≤ 2 sentences
  per symbol citing evidence ids. "Research" button per row opens F5 for that ticker.
- **Accept:** AppTest renders 3 symbols; explainer failure degrades to reason codes only.

#### F7. Events: LLM news analyst behind quarantine — PARTIAL (crash fixed, layout + "no agent" chip done)

- **Why:** every AI step should have an agent (user, 2026-11-21); news is the one Forecast step without one. Headlines are
  untrusted (S1, S16), so the agent may only see quarantine output.
- **Now:** `forecaster/news.py::NewsService` scores sentiment by keyword (`Story.sentiment`, `directional_bias`,
  `impact_score`); `forecast_tabs.render_events` shows stories via `st.text`. `orchestration/quarantine.extract_facts(raw, source,
  schema, client)` returns `Extraction(facts, flagged, error, trust="untrusted")`, rejects instruction-like output and blanks
  `topic` on flagged input. Pattern to copy: `schemas.EmailFacts` (closed enums, no free text).
- **Build:**
  1. `schemas.NewsFacts(Strict)`: `ticker` (ticker pattern or ""), `topic: Literal[earnings, guidance, product, legal,
     regulation, macro, management, deal, other]`, `sentiment: Literal[positive, neutral, negative]`, `materiality:
     Literal[low, medium, high]`. No free-text field.
  2. Slot `forecast.news_analyst` → `builtin-news-analyst` (templates role, skill `untrusted-content-handling`, no tools,
     temperature 0). The slot's "agent" call **is** `extract_facts` with the agent's model/temperature; its instructions are
     appended after quarantine's fixed `SYSTEM` (never replace it).
  3. `forecaster/news_analyst.py::analyze(stories, client, agent, limit=10)`: one `extract_facts` per headline
     (`title + " — " + publisher`), bounded by `FORECAST_SPECIALIST_CALL_SECONDS` total; returns per-ticker counts
     `{ticker: {positive, neutral, negative, high_materiality}}` plus `flagged` count. Nothing else leaves the function.
  4. Events UI: agent chip + *Analyze with AI* button; show the counts table next to the keyword leanings and say where they
     disagree. Headlines stay `st.text`.
  5. Optional, after review: feed counts into ToT as the `news` driver (`tot.FAMILY["news"]`), counts only.
- **Files:** `schemas.py`, `templates.py`, `slots.py`, new `forecaster/news_analyst.py`, `forecast_tabs.py`, tests.
- **Accept:** unit tests (hostile headline → facts None or topic blank; counts only); `evals/injection.py` run through
  `news_analyst.analyze` adds a row "News analyst: injected text reaching output" = 0 (hard); AppTest renders the counts and the
  hostile headline inert; `make eval` PASS.
- **Don't:** add a free-text field to `NewsFacts`, pass headlines to the critics, or render any model output with markdown.

#### F8. Screens and Technical drilldown — DONE (line chart + SMA; candlestick not added)

- Screens: sortable table + mini-charts, "Research" per row. Drilldown: candlestick + indicators chart, indicator table with plain
  explanations, unavailable inputs listed with why.
- **Accept:** AppTest for both with fake market data.

#### F9. Track record (was Validation) + System health — DONE

- Explain what is measured (directional accuracy, coverage, abstention, benchmark-relative return, naive baselines from
  `report.summarize`) and why it is empty until horizons elapse; per-forecast timeline. System health: provider status chips,
  last latency, which keys are configured (yes/no).
- **Accept:** AppTest with seeded synthetic forecast/outcome pairs.

#### F10. Chat selector lists every agent — DONE

- `main.py` agent selector: General + every `chat_capable` slot agent + every custom agent, grouped ("System", "Mine"). Routing
  stays as today (coordinator roles via `run_query(only=role)`, others through the v1 runtime).
- **Accept:** AppTest: a newly created custom agent and a tweaked built-in both appear and answer.

### Track P. Capstone feedback cards

### P1. Labelled stress set with an expected action (W5, W6)

- **Why:** W5 asked for ambiguous requests, incomplete records, stale market data and conflicting sources. W6 committed to
  "each case labelled in advance with answer / caveat / ask / escalate; passes only if the action matches and every figure is correct".
- **Now:** fault and conflict cases exist (`evals/faults.py`, 63 + 26) but have no expected *action*.
- **Build:**
  1. New `evals/stress.py` with `@dataclass StressCase(id, kind, query, records, market, expected_action, expected_claims)`.
     `kind ∈ {ambiguous, incomplete, stale, conflict, clean}`; `expected_action ∈ {answer, caveat, ask, escalate, refuse}`.
  2. `action_of(report, refused: bool) -> str`, a pure function and the **only** definition of action:
     `refuse` if refused by `policy.screen`; `ask` if any escalation kind is `clarify` (added in P3); `escalate` if any of
     `conflict, step_failed, ungrounded, unsupported, stale, untrusted_write`; `caveat` if the only kinds are `unavailable`;
     else `answer`.
  3. At least 40 cases, ≥ 8 per kind, reusing `golden.run_task`, `golden._rec`, `golden.run_task(..., age_days=N)` for stale data,
     `market={SYM: "unavailable"}` or `provider_mode="raise"` for outages). Incomplete = one or two months of a quarter (expect `caveat`). Conflict = same
     provider+period, different totals (expect `escalate`). Ambiguous = no period/category/ticker. Mark ambiguous cases
     `expected_action="ask"` and allow them to fail until P3 lands: put their ids in `PENDING_P3` and exclude them from the gate.
  4. Score per case: action matches **and** `golden.score`-style check of `expected_claims` passes.
  5. Run every case through B1 too (`golden.run_b1`) and report both.
  6. Tool-selection metric (W1 assessment): add `expected_agents: frozenset[str]` to every golden task and stress case; report
     "Planner chose the expected specialists" = share of tasks where `{s.agent for s in plan.steps} == expected_agents` (hard, ≥ 90%).
- **Files:** `evals/stress.py` (new), `evals/run.py`, `tests/test_evals.py`.
- **Accept:** `pytest tests/test_evals.py`; `make eval` shows rows "Stress set: action + figures correct (coordinated)" = 100%
  of non-pending cases (hard) and "Stress set, B1" (info).
- **Don't:** derive the label from the system's own output; write it in the case.

### P2. Verifier checks relevance, not just presence (W5, W6)

- **Why:** W5: "verifier only confirms the figure appears in the cited source". A number can appear in the wrong record.
- **Now:** `verifier.verify_claims` (`src/agents/orchestration/verifier.py`) accepts any cited text containing the number.
  `records_evidence` maps `storage_key → extraction JSON`.
- **Build:**
  1. Change `records_evidence` to return the record dict (category, provider, period_start/end, account_label, extraction),
     and keep a text view for the number check.
  2. For claim keys of the form `bill:<provider>:<period_end>` and `statement:<provider>:<end>`, require the cited record's
     provider and period_end to equal the key's, and the step's category (if the claim carries one) to equal the record's.
     Mismatch → `Escalation("unsupported", "<key>: cited record is <provider>/<period>, not the claim's")`.
  3. Aggregates (`quarter:*`, `cashflow_delta:*`): every cited record's period must fall inside the quarter.
  4. Update the `Escalation.kind` comment in `coordinator.py` to list `unsupported`, `clarify`, `single_source`, `missing_statement`.
  5. Single-source caveat (W6): an `outlook:*`/`thesis:*` or `bill:*` claim backed by exactly one provider / one document when the
     plan expected more (market: fewer than 2 evidence families present; bills: no second document for a corrected period) adds
     `Escalation("single_source", ...)`. In P1's `action_of`, `single_source` counts as `caveat`, like `unavailable`.
- **Files:** `verifier.py`, `service.py` (call site unchanged except the new evidence type), `tests/test_orchestration*.py`, `evals/stress.py`.
- **Accept:** unit tests for right figure/wrong period, wrong provider, wrong account; ≥ 5 seeded wrong-record stress cases
  `expected_action="escalate"`; `make eval` row "Evidence relevance: wrong-record citations caught" = 100% (hard; W6 target ≥ 95%,
  gate at 100% on synthetic data).
- **Don't:** let a model mark anything verified (it may only add `unsupported`).

### P3. Ambiguous requests ask one clarifying question (W2, W6)

- **Why:** W6 routing: "the system asks when the request is ambiguous (missing period, account or ticker)". Today `plan_for`
  guesses (latest quarter, all categories).
- **Build:**
  1. In `workers.py`, `ambiguity(query, records) -> Optional[str]` (pure, rule-based) returning one of a closed set:
     `period` (bill question, no month/quarter/"this"/"last" words), `account` (records of the asked category exist for
     ≥ 2 `account_label`s and none is named), `ticker` (market words, no ticker).
  2. `service.run_query`: if ambiguity is found, return a report with no steps and one `Escalation("clarify", <templated question>)`.
     Questions are fixed templates per type (no model text, invariant S16).
  3. UI (`interface/main.py::_respond_coordinated`): render the clarify question as the assistant reply; the user's next message
     is re-planned with both turns concatenated.
  4. Remove the P3 ids from `PENDING_P3` in `evals/stress.py`.
- **Files:** `workers.py`, `service.py`, `interface/main.py`, tests, `evals/stress.py`.
- **Accept:** unit tests per ambiguity type plus "not ambiguous" controls (no false asks on the 30 golden tasks: golden row unchanged);
  stress ambiguous cases pass; AppTest: clarify reply renders, no exception.
- **Don't:** ask more than one question per turn, or ask when the golden set already answers correctly.

### P12. Missing statements are observed and offered a sync (W1, W2)

- **Why:** W1: "check which statements exist before summarizing obligations, rather than inventing missing amounts". W2: a missing
  statement "triggers a portal fetch (or asks for confirmation)" so observations drive the next step.
- **Now:** `data_ingestion/pipeline.py::overdue(vault)` computes expected-but-missing bills from the ledger (`set_expectation`), and
  the Sync panel shows them. The expense worker (`workers.expense_worker`) never consults it.
- **Build:**
  1. Snapshot `overdue(vault)` on the calling thread in `service.build_coordinator` (invariant S15) and pass it to `expense_worker`
     like `get_records`.
  2. For the asked quarter/category, each overdue provider+period yields `Finding(claim_key="missing:<provider>:<period_end>",
     status="unavailable", value=None)` and `Escalation("missing_statement", <templated text>)`. Any quarter total touching it is
     `uncertain` (partial), never a guessed number.
  3. UI: the escalation renders a **Sync now** button that calls the existing Sync panel action. No automatic fetch: fetching stays
     an explicit user trigger (`rag_design.md` §5).
  4. Stress cases: missing month with a ledger entry → `caveat` plus the missing claim; no ledger entry → unchanged behaviour.
- **Files:** `workers.py`, `service.py`, `interface/main.py`, `evals/stress.py`, tests.
- **Accept:** unit tests (overdue → missing finding, no invented total); `make eval` row "Missing statements reported, never
  invented" = 100% (hard); AppTest renders the button.
- **Don't:** start a portal or Gmail fetch without a click.

### P5. Core Memory read path (W1 assessment, W2)

- **Why:** committed: "retrieve relevant approved memories when planning; ≥ 9/10 seeded memories retrieved later; corrections
  supersede; forgotten items never retrieved". Today approved items are only listed in the UI (`interface/agents.py:662`).
- **Build:**
  1. `memory.recall(vault, query, limit=5) -> list[CoreFact]`: only approved, not superseded, `trust == "trusted"` items
     (untrusted-approved items are returned with `trust="untrusted"` and labelled); match by key prefix/words, deterministic.
  2. New schema `CoreFact{key, value≤200, approved_at, trust}` with `extra="forbid"` in `schemas.py`.
  3. Coordinator use: memory facts go **only** into `render_report` as a separate "From your approved memory" section and into the
     expense step as an optional baseline (e.g. `budget_baseline:<period>`). They are **never** sent on any bus edge to
     market/forecaster/email/portal. Add this as invariant S18 in `platform_plan.md` §2.
  4. A `forget(token, item_id)` in `memory.py` using the same UI-minted token pattern as `approve`; forgotten items are deleted,
     not superseded.
- **Files:** `memory.py`, `schemas.py`, `service.py`, `workers.py::render_report`, `interface/agents.py`, tests.
- **Accept:** eval row "Core Memory recall: seeded facts retrieved in a later run" ≥ 9/10 (hard); "superseded or forgotten facts
  returned" = 0 (hard); disclosure eval still 0.
- **Don't:** add an agent-facing write path, or index memory into RAG (invariant S13).

### P4. Cross-agent and shared-memory leakage (W6)

- **Why:** W6: "examine whether information can unintentionally pass between agents or enter shared memory, even when individual
  components follow their assigned restrictions". Current checks are per edge (`bus.py`) and per write (`memory.py`), not composed.
- **Build** (extend `evals/disclosure.py`; new tests in `tests/test_composition_leaks.py`):
  1. **Domain canaries:** seed a unique canary per source domain (records account suffix, email sender, brokerage position size,
     memory value). For each golden + stress run assert each canary appears only in its allowed sinks. Sinks to scan: every bus
     payload, `agent_activity_log`, provider egress (`FakeProviders.requests`), each worker's *inputs*, `Finding.summary`,
     `render_report` output, pending and approved memory.
  2. **Multi-hop:** chains expense → market → coordinator and email → coordinator → memory proposal: assert no field arrives
     downstream that no edge schema on the path allows.
  3. **Merge as a channel:** `merge` output and `RunReport.claims` must not carry an untrusted claim with `trust="trusted"`; a merged
     claim mixing trusted and untrusted contributors is `untrusted` (already the rule; test it end to end).
  4. **Memory as a channel:** an untrusted-derived proposal, once approved, is recalled (P5) only with `trust="untrusted"`, never
     on a bus edge, and never into a quarantine prompt.
  5. **Allowed-sinks table** lives in `evals/disclosure.py` as data, so a new edge must declare its sinks.
- **Accept:** `make eval` row "Composed leakage: canaries outside allowed sinks (multi-hop, memory, merge)" = 0 (hard), with counts
  of runs, sinks and hops.
- **Don't:** widen a schema or add a sink to the allowed table to make a case pass.

### P6. Escalation metrics and reviewer decision log (W6)

- **Why:** W6 committed to escalation precision/recall ≥ 90% and a logged accept / correct / dismiss decision per finding,
  with repeated overrides feeding new eval cases.
- **Build:**
  1. In `evals/stress.py`, compute escalation precision and recall over cases where the expected action is `ask` or `escalate`
     (positive) versus `answer` or `caveat` (negative). Count escalations per run (info).
  2. UI: in the Coordinator reply, each escalation gets three buttons: Accept, Correct (opens a text field; the correction becomes
     a Core Memory **proposal**, never a commit), Dismiss.
  3. Log `{run_id, escalation_kind, claim_key, decision, ts}` (no values, no text) to `agent_activity_log` (`kind="review"`).
  4. `evals/review_cases.py`: a function that turns exported review entries with decision `dismiss` or `correct` into stub
     `StressCase`s for a human to label. Do not auto-label.
- **Files:** `evals/stress.py`, `interface/main.py`, `agents/store.py` (log kind), tests.
- **Accept:** eval rows "Escalation precision" and "Escalation recall" ≥ 90% (hard); AppTest clicks each button, log entry written
  with no claim values; `make test`.

### P7. Repeat-run and changing-condition stability (W6)

- **Why:** W6: "establish that the reported safety results remain consistent across repeated tests and changing conditions".
- **Build:** `evals/stability.py` and `make eval-stability RUNS=10`:
  1. Repeat the full `evals.run.evaluate()` `RUNS` times; record per-row pass rate and min/max of each numeric metric.
  2. Perturbations, each a seeded function applied to inputs (seed printed in the report): record order shuffled, duplicate
     re-uploads added, injection payloads paraphrased by template (case, spacing, homoglyph, word order), worker latency jitter
     0-200 ms, fault timing moved, `max_workers ∈ {1, 2, 4}`, `PERSONAL_AI_INFERENCE_CONCURRENCY ∈ {1, 2}`.
  3. Report `evals/results/stability_latest.md`: per row `pass_rate`, `variance`, worst seed. Gate: every hard row 100% across runs.
  4. Live variant (opt-in, user runs it): `make eval-stability LIVE=1` reruns `evals.live` against two worker models.
- **Files:** `evals/stability.py` (new), `Makefile`, `tests/test_evals.py` (RUNS=2 smoke).
- **Accept:** `make eval-stability RUNS=10` exits 0 and the report lists every hard row with pass rate 100%.
- **Don't:** treat a flaky row as noise. Any row below 100% is a bug or a finding to record in §5 below.

### P8. Held-out Tree-of-Thought evaluation and calibration (W4, W5, W6)

- **Why:** W4: thresholds tested only on 8 self-labelled cases. W5: compare forecast quality with the single-agent baseline.
  W6: Brier score on a held-out split must beat both baselines.
- **Build:**
  1. `evals/tot_synth.py`: a parametric generator `make_case(seed) -> (EvidenceSnapshot, critic_report|None, label)`. The label is
     the scenario planted by construction (e.g. family signals drawn around a chosen scenario with noise σ and a configurable
     number of contradicting families), not a human reading.
  2. 200 cases; seeds 0-99 = dev, 100-199 = held-out. Tune `TOT_*` (in `src/config.py`) on dev only, then freeze and record the values.
  3. Metrics on held-out, for ToT, linear (best depth-1) and critic-vote: accuracy; Brier score using p(scenario) = node scores
     normalised over the three scenarios (abstain = uniform 1/3); abstention rate.
  4. Persist `selected_id` / `runner_up_id` / `margin` with saved forecasts (`outcomes.create_forecast`, approve-to-save unchanged)
     so realized outcomes can later compare ToT with the heuristic.
  5. Keep the existing 8-case row as a regression check. Realized-outcome metrics (directional accuracy, coverage, abstention,
     benchmark-relative, naive baselines) already exist in `forecaster/report.py::summarize`; reuse them, do not reimplement.
- **Files:** `evals/tot_synth.py` (new), `evals/reasoning.py`, `evals/run.py`, `src/forecaster/outcomes.py`, tests.
- **Accept:** `make eval` rows "ToT held-out accuracy ≥ both baselines" (hard) and "ToT held-out Brier < both baselines" (hard);
  report states the dev/held-out split and the frozen thresholds.
- **Don't:** tune on held-out, or change thresholds after seeing held-out results (if you must, regenerate a new held-out range and say so).

### P9. Live local run (USER, agent assists) (W4, B1, B6, B19)

- **Steps for the user:** start oMLX; `make run`; Chat → Advanced → Rebuild RAG Index; one RAG question; one Coordinator
  cross-domain question; one ticker that converges and one that reaches depth 2; `make eval-live`.
- **Report back** with a [state_report.md](state_report.md) paste (redacted; never committed).
- **Record** in `platform_plan.md` §5: latency per step, peak memory (Activity Monitor), memory pressure, the profile from
  `ai_engine.md` "Memory budget", `eval-live` pass rate.
- **Agent's part:** fix anything that breaks (each fix gets a unit test) and update the numbers. Never paste model output containing
  personal data into docs.

### P10. Retrieval calibration on the real embedder (USER, agent assists) (W3)

- `PYTHONPATH=. python -m evals.rag.run --sweep` today sweeps only the abstention coverage threshold (`evals/rag/run.py:173`).
  Extend the sweep to `min_relevance` (0.1-0.6) and `k` (3-8), add a `--live` switch that uses the oMLX embedder instead of the
  hashed stand-in, then the user runs it with oMLX up.
- Choose the pair with the highest recall@5 subject to false-abstain ≤ 0.05, set it in `src/config.py`, and report before/after
  in `evals/results/rag_latest.md`.
- **Don't:** lower `min_relevance` just to make an answer appear (AGENTS.md).

### P11. Final submission packaging

- Re-run `make test`, `make eval`, `make eval-rag`, `make eval-stability`; paste the measured numbers into `platform_plan.md` §5
  and §1 of this file. Update `architecture.md` diagrams and the Miro board if a card changed the flow.
- The final write-up quotes: success-criteria rule (`platform_plan.md` §5 "How success is scored"), framework mapping
  (`architecture.md` §5), synthetic-data statement (above), and the honest limits in §5 below.

## 5. Known limits (state these in any write-up)

- Evaluation data is synthetic and hand-built (golden set 30 tasks: 10 cross-domain, 20 single-domain). Offline evals use fakes
  and a stand-in embedder; nothing is verified on live oMLX until P9/P10.
- Forecast scores are uncalibrated heuristics until P8, and realized-outcome evaluation needs elapsed 5-/21-session horizons.
- PII regexes are heuristic; schemas are the real control. The quarantine model's obedience is not relied on.
- The ChromaDB index is plaintext if persisted (in-memory today), see `security_design.md`.

## 6. After the capstone (not scheduled)

| Item | Note |
|---|---|
| LangGraph parity eval (optional) | Rebuild `plan_for` plans as a LangGraph `StateGraph` in `evals/` only; assert the same claims/escalations as `Coordinator.run` on the 30 golden tasks. No runtime dependency, no egress. Do this before any LangGraph adoption (`platform_plan.md` D13) |
| Thought tree as read-only MCP resource (W4 plan) | `tot://<snapshot_id>`; optional, mention as planned if the write-up cites it |
| News into ToT as quarantined, templated sentiment counts (was B17) | headlines are untrusted; injection eval must stay 0 |
| stock_predictor port gaps (was B18) | ATR/volume in `technical_score`, fundamentals provider, optional 63-session horizon (`forecaster.md` Part 1) |
| Optional LLM thought proposer | behind `Thought` validation, same call budget |
| Real provider portal recipes (R10) | supervised `playwright codegen` runs by the user |
| Key rotation / backup, vault migration | `security_design.md` "no migration path" |
