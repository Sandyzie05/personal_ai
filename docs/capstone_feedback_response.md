# Response to Week 1 and Week 2 feedback

Evidence is reproducible: `make test` (unit/contract tests) and `make eval` (golden set, fault injection,
injection corpus, disclosure canaries; writes `evals/results/latest.md`). All data synthetic.

## Week 1
| Feedback | Response | Where |
|---|---|---|
| Make integration of the two repos concrete | stock_predictor fully absorbed (gap table; FastAPI/DB dropped, logic ported to pure services over typed contracts) | `platform_plan.md` §1, `src/forecaster/` |
| How coordinator delegates / combines / handles disagreement | Typed `Plan` (allowlisted agents, dependencies), parallel waves, deterministic `merge` (conflicts reported, never averaged), one bounded re-check, then `unresolved` + user escalation | `src/agents/orchestration/coordinator.py` |
| Measurable criteria: parallel delegation, grounding, bounded recovery | `parallel_overlap` + wall-vs-sum-of-steps ratio; every claim carries evidence ids or an explicit status; retries only for classified-transient errors, per-step timeout, round/deadline caps | `platform_plan.md` §4, `evals/` |
| Reflective: benefit over running each system alone? | Golden set run three ways (B1 single agent, B2 sequential coordinator, coordinated); cross-domain tasks are the discriminator | `evals/run.py` -> `latest.md` |

## Week 2
| Feedback | Response | Where |
|---|---|---|
| Compromised email / misleading portal must not influence downstream decisions | Quarantine to tool-less schema extractor; allowlist-regex strings; taint propagation; readers have no write tools; write/memory from tainted data needs explicit acknowledgement | `quarantine.py`, `memory.py` |
| Test conflicting records and unavailable live data | Same account/period with different totals -> `unresolved` + escalation; provider outage -> `unavailable` finding, no lean produced | `test_orchestration*.py`, `evals/faults.py` |
| Escalation rules for unresolved conflicts | conflict, unavailable/uncertain, stale (`max_age`), failed/skipped step, untrusted-derived write | `coordinator.escalations_for` |
| Core Memory changes only after explicit approval | agents only `propose`; `approve(token)` (single-use token minted by the UI handler) reachable only from the Core Memory UI tab; supersede not append | `memory.py`, `interface/agents.py` |
| Messages contain only needed personal info | per-edge `extra="forbid"` schemas + PII/canary scan + audit of field names/hash | `bus.py`, `evals/disclosure.py` |

## Honest limits
Heuristic PII regexes (schemas are the real control); LLM extractor obedience is not relied on; no live portal scraping;
forecasts are uncalibrated heuristics evaluated only after horizons elapse; oMLX path untested against a live server.
