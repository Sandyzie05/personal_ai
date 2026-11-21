# Forecaster (`src/forecaster/`)

Part 1 is the forecasting system: modules, rules, UI. Part 2 is the Tree-of-Thought (ToT) reasoning design, and why every
other workflow stays linear. Remaining work: [roadmap.md](roadmap.md) (P8, P9, §6). Index: [README.md](README.md).

## Part 1. Forecasting system

Research support for US stocks and ETFs: gather dated public evidence, freeze it, let independent specialists review it, and (only if you choose)
save the forecast so it can be scored after its horizon. **It never trades and never promises returns.** Scores are uncalibrated heuristics.

### Flow

```
ticker ─► validate ─► providers (public, fixed routes) ─► EvidenceSnapshot (immutable, hashed)
                                                              ├─► research prediction (heuristic, 5 / 21 sessions)
                                                              └─► ToT thesis search (tot.py): ≤6 theses ─► rubric ─► scenario-diverse beam (3)
                                                                    └─ only if not decisive: ScenarioSwarm critics (macro · valuation · event · downside,
                                                                       parallel, one shared review) ─► re-score / prune ─► select or abstain
      ─► thesis:<SYM> finding (uncertain when close, critic-less, or contradicting the heuristic)
      ─► [optional, you approve] save forecast ─► evaluate after horizon
```
### Modules

Trader (intraday plan, advice only): see [trader.md](trader.md).

| Module | Role |
|---|---|
| `providers.py` | `PublicDataProviders`: fixed allowlisted routes (Yahoo chart + news search, Alpha Vantage, FRED, SEC EDGAR), timeouts, size caps, retry only transient errors, queries containing personal identifiers are refused. Returns typed results with explicit `completed / partial / unavailable`. |
| `models.py` | Immutable contracts (`ResearchRequest`, `EvidenceSnapshot`, `ResearchPrediction`, ...). |
| `research.py` | `ResearchPredictionService`: parallel market/macro/filing fetch, scoring, `abstained`/`unsupported` states. |
| `scenarios.py` | `ScenarioSwarmService`: 4 specialist reviews on the same frozen snapshot, bounded workers, deadline, model-call budget. |
| `tot.py` | **Tree-of-Thought thesis search** (Part 2): competing bull/base/bear theses per evidence family, deterministic rubric, beam with a diversity slot, specialist critics only when depth 1 is not decisive, pruning with recorded reasons, conservative tie-break, abstention. Coordinator path only for now; the Forecast page still shows the raw swarm verdict. |
| `technical.py` | Deterministic indicators from closes (SMA/EMA/RSI/MACD/Bollinger, trend, support/resistance, volatility). ATR(14) and 20-bar volume ratio use `PriceBar.high/low/volume` (Yahoo/Alpha parsed; misaligned arrays dropped) and are reported as unavailable when absent. News failures carry their own `yahoo-news` SourceId. |
| `recommend.py` | **Research lean** (bullish/neutral/bearish + confidence + risk) from technicals and evidence; ranking and diversified selection. Never buy/sell wording; `insufficient_evidence` when inputs are missing. |
| `screens.py` | Period high/low, price-position (valuation proxy) and momentum screens over a bundled 50-name universe. States its window; no fundamentals. |
| `news.py` | News search and "today" report; stories are untrusted data (sanitised, length-capped), conflicting signals are surfaced as disagreement, never netted. |
| `outcomes.py`, `report.py` | Forecast records, outcome evaluation vs SPY, scoreboard, trend, CSV export (formula-injection safe), naive baselines, "insufficient sample" below 10. |
| `health.py` | Per-provider status/latency/failure counts shown in the UI. |
| `themes.py`, `events.py`, `filings.py`, `macro.py`, `source_registry.py`, `cache.py` | AI-infrastructure theme exposure, event extraction, SEC filing and FRED signals, source provenance, short-lived cache. |

### Correctness rules

- **Point in time:** evidence is stamped and frozen; historical (`as_of`) research is unsupported rather than faked.
- **No leakage:** an outcome counts only after its horizon elapsed and only if evaluated after the target close. Horizons 5 and 21 sessions are scored separately.
- **Explicit uncertainty:** provider outage, abstention, stale data or specialist disagreement lowers confidence and is shown; it is never hidden.
- **Metrics:** directional accuracy, return-estimate error, coverage/abstention rate, always-up and persistence baselines. Small samples report "insufficient sample".

### Bounds and privacy

Local inference is capped app-wide at `PERSONAL_AI_INFERENCE_CONCURRENCY` in-flight requests (default 2), and specialists can use the smaller `PERSONAL_AI_OMLX_WORKER_MODEL` (see [ai_engine.md](ai_engine.md) "Memory budget"). Max 5 orchestration rounds, shared tool/model-call budgets, 45 s research and 60 s scenario deadlines, 4 specialist workers (`config.FORECAST_*`).
Only tickers, topics and fixed parameters leave the machine; specialists run on the local model. Saved forecasts and outcomes live in the encrypted vault,
and saving needs an explicit click bound to the exact content shown.

### In the UI (Forecast page)

Sections: **Research** · **Watchlist** · **Events** · **Screens** · **Drilldown** · **Track record**. Nothing is fetched on page
load; each section runs on a button, and Watchlist/Screens/Drilldown rows have *Research this ticker*.

Research is one flow: **1 Evidence** (outlook cards, price chart with SMA 20/50, source status, "why" and provenance in expanders)
→ **2 AI review** (agent chips; *Run AI review* runs the 4 critic agents in parallel, then the ToT search with their report as
the critic, then the thesis explainer) → **3 Verdict** (selected scenario, margin, runner-up, uncertainty flags, explainer
text) → **4 Track** (optional; needs the attested session sequence from "Advanced: outcome tracking").

### Workflow agents (slots)

Every AI step is a slot in `src/agents/slots.py` with a fixed default agent from `templates.py`:

| Slot | Agent | Output (validated in code) |
|---|---|---|
| `forecast.critic.{macro,valuation,event,downside}` | `builtin-critic-<role>` | `scenarios.ModelOpinion` → `SpecialistOpinion` (identity, scenario and source ids filled by code) |
| `forecast.thesis_explainer` | `builtin-thesis-explainer` | `explain.ThesisExplanation` (cites only the thesis' evidence ids) |
| `forecast.watchlist_explainer` | `builtin-watchlist-explainer` | `explain.WatchlistExplanation` (only listed symbols) |

The user tweaks these agents in *Agents → Forecast workflow* (model, temperature, max tokens, extra instructions, skills); tweaks
are vault overrides (`agent_overrides`), *Reset* removes them. Fixed by the slot: no tools, the output schema, the budget
(`FORECAST_SPECIALIST_CALL_SECONDS` × ⌈4 / `PERSONAL_AI_INFERENCE_CONCURRENCY`⌉, max tokens ≤ `FORECAST_MODEL_MAX_TOKENS`), and
the prompt order *slot rules → skills → role rules → extra instructions → shared safety rules*. Explainer output may not contain
a number absent from its input. A rejected answer records a closed `invalid_reason` (`empty | not_json | truncated | schema |
unknown_evidence`); `<think>` blocks and code fences are stripped first. Each run logs slot, agent, status, latency and reason
(no text) to the activity log. Events has **no** agent yet: headlines must go through quarantine first (roadmap F7).

### Known limits and stock_predictor port gaps (reviewed 2026-11-02)

stock_predictor's FastAPI/SQLAlchemy/Redis layers were dropped on purpose. These are the gaps in the
*reasoning* that was ported:

| stock_predictor | Here | Gap | Disposition |
|---|---|---|---|
| `research_prediction.py` | `research.py` | 63-session horizon dropped; news score never fed in (`event_strength` is always 0, because headlines are untrusted text and stay UI-only); buy/sell labels, risk factors and signal breakdown dropped | Keep news out of scoring until it can enter as quarantined, templated sentiment counts ([roadmap.md](roadmap.md) §6); horizons stay 5/21 |
| `scenario_swarm.py` | `scenarios.py` + `tot.py` | Original only critiqued one fixed direction, with nothing generated, compared or pruned | **Closed by ToT** (Week 4): specialists now act as critics over competing theses |
| `recommendation_engine.py`, `market_intelligence.py` | `recommend.py`, `news.py` | Fundamental score, valuation bonus, idea ranking and swarm-adjusted ranking dropped; `technical_score` ignores the ATR/volume that `technical.py` computes | [roadmap.md](roadmap.md) §6; ranking stays UI-only |
| `stock_lists.py` | `screens.py` | Valuation from a price z-score, not P/E or P/B; "period" high/low, not all-time; fixed 50-name universe | Documented proxies; fundamentals need a new provider ([roadmap.md](roadmap.md) §6) |
| `local_model_analysis.py` | none | Single-call thesis review not ported | Superseded by ToT critics |
| `prediction_tracker.py` | `outcomes.py` | Nothing records forecasts or evaluates outcomes automatically; UI-only, explicit approval | Intentional (approval-gated persistence) |

Coordinator workers consume only `research.py`, `tot.py`/`scenarios.py` and the technical lean.
`recommend.rank_leans`, `screens` and `report` are Forecast-page features.

## Part 2. Reasoning design: where the platform branches, and where it does not

Status 2026-11-02: implemented in `src/forecaster/tot.py`, wired into the coordinator's forecaster worker,
tested in `tests/test_forecaster_tot.py` and `tests/test_orchestration_workers.py`, and measured in `make eval`
(`evals/reasoning.py`). Nothing here has run against a live oMLX model yet. Scores are uncalibrated heuristics.

### 1. Decision: Tree of Thought (ToT) only in forecasting

| Workflow | Reasoning | Why |
|---|---|---|
| Records / bills (expense worker) | Linear, deterministic code | The answer is a lookup plus arithmetic. Branching adds latency and a chance to pick a wrong "alternative" total. Conflicts already surface as `unresolved`. |
| Coordinator planning | Rule-based plan + validation (optional LLM planner behind `validate_plan`) | Few, enumerable specialists. A bad plan fails visibly through escalations, and budgets are fixed in code (§6). |
| Document Q&A (RAG) | Advanced RAG, at most one bounded re-retrieve | Platform decision D8: free-running retrieve/reflect loops increase injection surface and latency. |
| **Forecaster thesis** | **ToT: beam search over competing theses** | High branching (bull/base/bear × evidence families), conflicting evidence, and a high cost of premature commitment. |

**Why linear reasoning fails in the forecaster.** The heuristic scorer and each specialist commit to one
direction in a single pass. A strong price trend anchors the read; macro or filing evidence that points the
other way only lowers a number instead of being judged as a rival explanation. Before this change,
`forecaster_worker` emitted the heuristic outlook and the specialist verdict as two unrelated claims, so a
"bullish" outlook could sit next to a "contradicts" verdict with nothing reconciling them. The stock_predictor
original had the same shape: specialists critiqued one fixed idea direction, and nothing generated or
compared alternatives.

### 2. Tree structure

| Element | Definition in code |
|---|---|
| Root (state) | One frozen `EvidenceSnapshot` (`snapshot_id` = SHA-256 of its content). Every node reasons over the same evidence, so branches cannot drift apart. |
| Thought | `Thought{scenario ∈ bullish/base/bearish, driver ∈ trend/volatility/macro/filing/theme/news/balance, evidence_ids}`: one way of explaining the evidence, citing the cards it rests on. Closed enums only, so no free text crosses a trust boundary. |
| Node | `Node{node_id, parent_id, depth, thought, score, components, status, reason}`. `node_id` = hash(parent, thought), so the tree is deterministic and replayable. |
| Branch | One reasoning path: a depth-1 thesis and its critic-reviewed child. |
| Depth | 1 = thesis scored by deterministic rubric; 2 = the same thesis re-scored after the LLM specialists review it. Limit `TOT_MAX_DEPTH = 2`. |
| Branching factor | ≤ `TOT_MAX_BRANCHES = 6` at depth 1 (one thesis per directional evidence family, plus the "signals cancel" base thesis). Beam width `TOT_BEAM_WIDTH = 3` at depth 2. At most 9 nodes. |

**Termination.** The search stops at the first of:

- `no_evidence`: no evidence families exist.
- `converged`: after depth 1, the top branch scores ≥ 0.75 and beats the best other-scenario branch by ≥ 0.20. The LLM is not called.
- `depth_limit`: depth 2 is done.
- `all_pruned`: every branch was pruned.

**Final output.** The best surviving node is selected if it scores ≥ `TOT_SELECT_MIN = 0.5`; otherwise the
search abstains. The output is a `thesis:<SYM>` Finding. Its summary is templated and records the selected
scenario and driver, the runner-up, the number of branches pruned, and any disagreement with the heuristic
outlook.

### 3. Search strategy: beam search

Beam search fits because local inference on a laptop is the bottleneck:

- **BFS** would ask the LLM critics about every branch: up to 6 × 4 = 24 calls on a 27B local model.
- **DFS** commits to the strongest first impression, which is exactly the failure described in §1.
- **Monte Carlo sampling** needs many cheap rollouts, which a local model cannot provide.

Two adaptations:

1. **Scenario-diverse beam.** The beam keeps the best surviving branch *per scenario* first, then fills the
   remaining slots by score. If only one scenario survives, one slot is still given to the best
   other-scenario branch (the diversity slot).
2. **Lazy, shared critic.** The specialist swarm (macro, valuation, event, downside) runs once per search,
   on the shared snapshot, and only when depth 1 is not decisive. Its four opinions score every surviving
   branch. Cost is ≤ 4 LLM calls per symbol, the same budget as before, and 0 when the evidence is
   unanimous.

### 4. Evaluation, pruning and selection

| Stage | Evaluator | Rubric (0..1) |
|---|---|---|
| Depth 1 | Deterministic heuristic checks (code) | 0.55 × **support** (signed agreement of all directional evidence families with the thesis; for base, how much they cancel) + 0.25 × **breadth** (share of families agreeing) + 0.20 × **freshness** (share of cited evidence ≤ 30 days old) |
| Depth 2 | Specialist critics (LLM) + code | 0.6 × parent score + 0.4 × **critic** (confidence-weighted agreement of critic scenarios with the thesis; a critic that cited the thesis's own evidence counts double) |

The critic opinions are already schema-validated and checked against real snapshot evidence IDs by
`ScenarioSwarmService` before the tree sees them. Invented evidence makes an opinion `invalid` and drops it.

**Pruning and failure conditions.**

- Soft prune below `TOT_PRUNE_MIN = 0.35`. The diversity slot is exempt.
- Hard prune ("critics contradict") when ≥ 2 critics with confidence ≥ 0.6 hold the opposite scenario.
- Branches outside the beam are marked `outside beam`, not deleted.
- If critics are unavailable, depth-2 nodes keep their parent score and are tagged `critic unavailable`. The
  finding becomes `uncertain`; no review is invented.
- A selected score below 0.5 means abstention.
- The finding is `uncertain` (and the coordinator escalates) when any of these holds:
  - the margin to the runner-up is < 0.10;
  - the thesis contradicts the heuristic outlook;
  - the critics were missing.

**Ties.** Scores within `TOT_TIE = 0.05` are tied. The tie goes to the thesis with broader evidence, then to
the more conservative scenario (base, then bearish, then bullish). This is deliberately not a vote: the
critics already contribute through their score, and conservative tie-breaking suits a research tool.

**Compute bounds.** All of these are in `src/config.py`:

- ≤ 9 nodes.
- ≤ 1 critic round (≤ 4 LLM calls, under the existing `FORECAST_MODEL_CALL_BUDGET = 8` and 60 s deadline).
- Coordinator step timeout = research deadline + specialist deadline + 5 s.
- App-wide in-flight request cap `PERSONAL_AI_INFERENCE_CONCURRENCY` (default 2). Critics can run on a smaller
  `PERSONAL_AI_OMLX_WORKER_MODEL`. Several tickers or agents never add a second model or more than N concurrent KV
  caches. See [ai_engine.md](ai_engine.md) "Memory budget".
- The coordinator deadline covers that step plus one dependent wave.

### 4a. Held-out evaluation and calibration (P8, 2026-11-21)

- **Generator** (`evals/tot_synth.py`): the label is planted first (scenario = seed % 3), then evidence is drawn around it:
  trend drift d·U(0.02, 0.10) + 0.6% noise per step; 3 families with signal d·U(0.3, 0.9) + N(0, 0.35), 0-2 sign-flipped,
  |signal| < 0.15 reads neutral; 4 critics each right with p = 0.6, confidence U(0.4, 0.9); 10% of cases without critics.
- **Split:** dev seeds 0-99 for all tuning; 100-199 burned (first held-out; raw Brier lost to critic-vote); gated held-out 200-299.
- **Rubric fix found on dev:** directional support ignored neutral families, so one weak card among neutral ones scored 1.0 and
  converged without critics. Support is now × (directional families / all families). `TOT_CONVERGE_SCORE` 0.75 → 0.8 (dev grid);
  every other `TOT_*` unchanged.
- **Calibration:** p(scenario) ∝ score^k, k fitted per method on dev only (ToT 7, linear 2, vote 5).
- **Held-out (200-299):** accuracy ToT 0.76 / linear 0.59 / vote 0.70; Brier (calibrated) 0.414 / 0.488 / 0.430; abstention
  0.02 / 0.00 / 0.03. **Limit:** on raw normalised scores critic-vote has the better Brier (0.378 vs 0.473).
- Saved forecasts carry `thesis_selected_id`, `thesis_runner_up_id`, `thesis_margin`, `thesis_scenario` (optional; old ids
  unchanged) so realized outcomes can later compare ToT with the heuristic.
- **Robinhood evidence (Track R):** new card types `fundamental`, `earnings`, `options` and ToT drivers `valuation`,
  `earnings`, `options` when a Robinhood MCP server is connected; the heuristic score is unchanged by them.

### 5. Roles and implementation tools

| ToT role | Implementation here | Course tool it corresponds to, and why it is not imported |
|---|---|---|
| Thought generator | `tot._drivers` + seeding in `search` (deterministic code agent). An LLM proposer is a planned extension behind the same `Thought` contract (≤ 2 extra theses, evidence IDs validated). | **CrewAI** role separation: the generator is a distinct role with one goal and no tools. Its prompt-level counterpart is the `builtin-forecaster` template in `templates.py`. |
| Critic / evaluator | Rubric in `tot._heuristic` / `tot._critic`, plus `ScenarioSwarmService` (4 LLM specialists, parallel, schema-checked) | **CrewAI** crew of critics: four roles evaluating one shared task in parallel. |
| Controller | `tot.search` + `_beam` / `_select`: beam policy, convergence, pruning, tie-break, abstention | **LangChain / LCEL** control flow: a `RunnableBranch`/`RunnableParallel`-style pipeline, written as a plain-Python loop. |
| Memory / state manager | Immutable `ThoughtTree` (content-hashed nodes, rooted at `snapshot_id`), returned with the run. It is persisted only through the existing preview → approve encrypted forecast save. | **MCP** shared state: the tree is a serializable contract that can be exposed as a read-only MCP resource (`tot://<snapshot_id>`) for the verifier or coordinator. That exposure is not built yet. |

**Why no framework import.** The tools are mapped, not imported. Platform rules forbid adding a second
orchestration framework, every inference call must stay on the loopback oMLX client, and the security
invariants (typed edges, templated summaries, budgets in code) are enforced by this repo's code and tests.
CrewAI and LangChain would add dependencies and hidden prompts without adding a capability.

### 6. Risk and mitigation

**Risk: weak evaluation signals prune the best branch.** Depth-1 scores are uncalibrated heuristics, and a
local model's critics are noisy.

**Mitigations:**

- the scenario-diverse beam;
- the diversity slot;
- hard pruning only for strong, multi-critic contradiction;
- every pruned node kept with its reason, so a wrong prune is auditable;
- an ablation eval that fails the build if ToT ever scores below either baseline.

Branch explosion is bounded by the caps in §4.

### 7. Evidence (offline, synthetic)

`make eval` row "ToT thesis selection" runs 8 labeled synthetic cases (`evals/reasoning.py`). The labels are
the designer's stated correct reading of each evidence-plus-critic configuration. This is a regression and
ablation check, not market accuracy.

| Policy | Correct |
|---|---|
| ToT (beam, critics) | 8/8 |
| Linear: commit to best depth-1 thesis, no critics | 6/8 |
| Critic majority vote only | 7/8 |

ToT reached the critics in 3 of 8 cases. The other 5 converged without LLM calls. The largest tree had 7
nodes.

**Not yet measured:**

- live oMLX critic quality;
- whether ToT improves realized 5- or 21-session outcomes (needs elapsed horizons, see
  Part 1);
- the critic-veto threshold on real model outputs.

### 8. Next steps

Planned in [roadmap.md](roadmap.md): P8 (held-out eval, Brier, persisting node ids) and P9 (live run).
Optional LLM thought proposer: roadmap §6. Revisit weights and thresholds only with outcome data, never to make one case pass.
