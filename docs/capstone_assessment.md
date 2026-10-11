# personal_ai: Capstone Readiness Assessment

## Findings from both projects

These findings describe inspected source code, not verified runtime behavior. Existing capabilities are foundations; proposed agentic behavior is not yet implemented.

| Project | Existing foundation | Important limitation |
|---|---|---|
| `personal_ai` | Authenticated local interface, encrypted vault, document ingestion and classification, structured-record retrieval, Chroma-based RAG, encrypted conversation history, source-grounded chat. | Current flow is retrieval followed by a model response, not an agent choosing tools and iterating toward a goal. Conversation history is not retrievable task-outcome memory. |
| `stock_predictor` | Market-data collection, provenance-bearing research evidence, rule-based predictions, optional local-model analysis, specialist scenario reviews, prediction snapshots and realized-outcome tracking. | Forecasts are heuristic, not demonstrated trained or calibrated models. Specialist reviews execute sequentially. Outcome tracking is not a complete leakage-safe historical backtesting system. |

Key source references:

- Assistant retrieval and response flow: [`src/ai_engine/chat_engine.py`](../src/ai_engine/chat_engine.py), especially lines 89–156, 285–471, and 507–561.
- Conservative query analysis: [`src/ai_engine/query_analysis.py`](../src/ai_engine/query_analysis.py), lines 45–123.
- Model interfaces and configuration: [`src/ai_engine/ollama_client.py`](../src/ai_engine/ollama_client.py), lines 57–116; [`src/config.py`](../src/config.py).
- Privacy boundaries and documented residual risks: [`security_design.md`](security_design.md).
- Legacy forecasting rules and fixed price targets: [`prediction_engine.py`](../../stock_predictor/app/services/prediction_engine.py), lines 210–301.
- Research scoring and 5-/21-day horizons: [`research_prediction.py`](../../stock_predictor/app/services/research_prediction.py), lines 297–365.
- Specialist scenario roles and aggregation: [`scenario_swarm.py`](../../stock_predictor/app/services/scenario_swarm.py), lines 17–104 and 170–254.
- Forecast outcome tracking: [`prediction_tracker.py`](../../stock_predictor/app/services/prediction_tracker.py), lines 15–156.
- Reusable API: [`stocks.py`](../../stock_predictor/app/api/routes/stocks.py), lines 151–207; [`market.py`](../../stock_predictor/app/api/routes/market.py), lines 55–79.

Sibling-project links assume both repositories remain adjacent.

## Coursework concepts: gaps, implementation, and evidence

The numbers below are proposed acceptance targets, not measured results.

| Concept | Current support / gap | Minimum implementation | Demonstration and success metric |
|---|---|---|---|
| Tool calling | Python services already exist; neither project exposes a general model-selected tool execution loop. Structured JSON generation alone is not tool calling. | Allowlisted, schema-validated tools for vault search, structured-record lookup, stock research, scenario analysis, and approved task-summary storage. Preserve citations and explicit errors. | On 20 labeled tasks, at least 18 choose the appropriate tool sequence and use its returned evidence. Every executed call passes argument and permission validation. |
| Reasoning architecture / ReAct | Assistant follows a fixed retrieval-to-answer flow. Forecast services expose some failures and degraded coverage, but no general replanning loop. | Coordinator repeatedly selects an action, observes the tool result, and updates task state. Limit each task to five orchestration steps and one retry per failing tool; count all calls against a separate budget. | Inject timeouts, malformed responses, and missing evidence. Every case terminates within its budget; at least 9 of 10 recoverable cases complete correctly. Unrecoverable cases explicitly abstain or request input. |
| Knowledge and memory | Assistant has document RAG and encrypted chat history; forecaster retains evidence and prediction snapshots. No retrieval of approved past task outcomes. | Store approved goals, preferences, source references, corrections, and outcomes in encrypted task records. Retrieve relevant records when planning; distinguish facts, forecasts, and expired information. | Retrieve at least 9 of 10 seeded task memories in a later conversation. Corrections supersede old values; forgotten or inaccessible memories are never retrieved. |
| Further reasoning | Forecast specialists critique shared evidence, but there is no explicit branching search. | Compare at most three candidate plans or bullish/base/bearish scenarios using a stated evidence-and-risk rubric, then select or abstain. | Demonstrate a case where contradictory evidence changes the selected plan or scenario. Record alternatives, selection criteria, and concise evidence-backed rationales, not private chain-of-thought. |
| Multi-agent coordination | Forecaster has specialist roles, executed sequentially; assistant has no delegation layer. | Coordinator delegates to Personal Assistant and Forecaster agents. Run independent forecast specialists concurrently over one immutable, bounded evidence packet; aggregate results deterministically. | Mixed task shows both core agents contributing. Execution timestamps demonstrate overlapping specialist calls; failed specialists remain visible and lower coverage rather than disappearing. |
| Safety | Assistant already protects authentication and encrypted primary storage. Chroma document text and some metadata remain plaintext under documented local restrictions. Forecast disclaimers are not authorization controls. | Read-only research by default; no trading, payments, arbitrary shell commands, or medical diagnosis. Gate persistent task writes with approval. Separate private vault access from public-data tools; treat retrieved text as untrusted. | Adversarial tests cover injected document instructions, invalid arguments, locked-vault access, unapproved writes, and private-data exfiltration. No unauthorized action succeeds. Logs exclude raw private content and secrets. |

## Minimal integrated architecture

```text
User → Coordinator → Personal Assistant → existing vault / RAG / records
                   → Forecaster → existing stock research services
                                → parallel specialist scenario reviews
     ← evidence, sources, uncertainty, visible failures, approved next steps
```

Use `personal_ai` as the user-facing application. Wrap existing retrieval methods instead of replacing its vault or RAG. Connect to the stock project's existing research API through a narrowly scoped local adapter; compose scenario services where no dedicated endpoint exists. Keep permission checks outside model discretion.

The Forecaster receives symbols, horizons, and public evidence, not passwords, encryption keys, raw health records, or financial statements. The coordinator combines outputs locally. Market providers may require internet access; local inference does not make the whole system offline. Verify model endpoint configuration and prevent forwarding private prompts to remote providers.

Retain the security design's existing Chroma residual-risk decision rather than describing all stored data as encrypted. Do not automatically put private task records into that plaintext index; begin with bounded retrieval over decrypted, authorized task records in memory.

## Build order and scope

1. Define two end-to-end tasks and tool contracts. Example: identify documented obligations; research one ticker at a supported horizon. Add schema, permission, provenance, and error tests.
2. Add bounded orchestration, encrypted task memory, correction handling, and explicit recovery.
3. Integrate the forecaster, parallelize independent scenario reviews, add bounded alternatives, and evaluate the complete workflow.

Scope the capstone to document-backed assistance and stock-market research, not autonomous life management or trading. A representative mixed task is: “Summarize my recorded obligations, then explain the evidence and risks for a ticker on my watchlist.” Keep obligations and uncertain market estimates separate; do not infer investment suitability from incomplete personal records.

## Forecast evaluation

Freeze each forecast's evidence and data cutoff. Evaluate 5-day and 21-day horizons separately, using chronological splits and point-in-time inputs; publication dates alone do not eliminate revised-data leakage. Compare heuristic and agent-assisted variants against identical simple baselines and a market benchmark.

Report directional accuracy, return-estimate error, probability calibration where applicable, coverage, abstention, and realized benchmark-relative outcomes. Treat existing confidence/probability fields as uncalibrated until evaluated. If simulating investment performance, include transaction costs. Report sample size and uncertainty; a short course project cannot establish reliable profitability.

The principal capstone outcome is demonstrably safer, evidence-grounded multi-step assistance, not a promise of superior stock returns.
