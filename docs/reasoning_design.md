# Reasoning design: where the platform branches, and where it does not

Status 2026-11-02: implemented in `src/forecaster/tot.py`, wired into the coordinator's forecaster worker,
tested in `tests/test_forecaster_tot.py` and `tests/test_orchestration_workers.py`, and measured in `make eval`
(`evals/reasoning.py`). Nothing here has run against a live oMLX model yet. Scores are uncalibrated heuristics.

## 1. Decision: Tree of Thought (ToT) only in forecasting

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

## 2. Tree structure

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

## 3. Search strategy: beam search

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

## 4. Evaluation, pruning and selection

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
- The coordinator deadline covers that step plus one dependent wave.

## 5. Roles and implementation tools

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

## 6. Risk and mitigation

**Risk: weak evaluation signals prune the best branch.** Depth-1 scores are uncalibrated heuristics, and a
local model's critics are noisy.

**Mitigations:**

- the scenario-diverse beam;
- the diversity slot;
- hard pruning only for strong, multi-critic contradiction;
- every pruned node kept with its reason, so a wrong prune is auditable;
- an ablation eval that fails the build if ToT ever scores below either baseline.

Branch explosion is bounded by the caps in §4.

## 7. Evidence (offline, synthetic)

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
  [forecasting_system.md](forecasting_system.md));
- the critic-veto threshold on real model outputs.

## 8. Next steps

1. Live run on the personal laptop: one ticker with unanimous evidence (expect converged, 0 calls) and one
   with mixed evidence (expect depth 2). Record both trees.
2. Optional LLM thought proposer behind `Thought` validation, counted in the same call budget.
3. Persist the selected/runner-up node IDs with saved forecasts so the outcome tracker can compare ToT with
   the heuristic after the horizon.
4. Revisit the weights and thresholds only with outcome data, never to make one case pass.
