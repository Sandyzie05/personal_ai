# Agentic Personal AI: High-Level System Design

Status: proposed capstone architecture, not a claim of implemented behavior.
Baseline: `personal_ai` main at `3f8d996`; inspected `stock_predictor` at
`11f325f`. Runtime correctness and forecast quality remain unverified.

### Implementation boundary

This branch extracts public-market research, parallel specialist reviews and
session-aware outcome evaluation into `src/forecaster/`, with a Forecast page
in the existing authenticated application. Saving displayed forecasts and
outcomes is explicit and uses immutable encrypted vault records.

Research and outcome tracking do not constitute the complete architecture
below. Coordinator delegation, general model-selected tool orchestration,
approved task-outcome memory and historical point-in-time evaluation remain
proposed. Session schedules are caller-attested, not independently checked
against an exchange calendar; outcome prices are supplied explicitly. There
is no automatic or scheduled outcome evaluation, live-news adapter, or
SEC/FRED historical-vintage support. Provider and model behavior is covered
with offline doubles, not a claim of successful live integration.

Trackable research captures its caller-attested session schedule before
fetching data. Same-day bars are eligible only after that session's supplied
close, including early closes. Forecast specialists use loopback inference
with redirects and environment proxies disabled and finite I/O timeouts.
These are inactivity timeouts, not hard cancellation: a continuously active
or trickling model response can outlive the awaited run deadline.

All original files in both projects are retained at the user's request.

## 1. Purpose and scope

Build one local, single-user application with two coordinated capabilities:

- **Personal Assistant:** answer questions about authorized documents and
  structured records; remember explicitly approved preferences and outcomes.
- **Forecaster:** research a requested stock symbol, compare evidence-backed
  scenarios, and evaluate forecasts after their horizons have elapsed.

`personal_ai` owns the interface, authentication, encrypted persistence and
orchestration. Extract the required forecasting components from
`stock_predictor` into this application; do not require its separate web app,
database, deployment scripts or background reporting service.

Initial scope is requested-symbol research at 5- and 21-trading-session
horizons, scenario comparison and outcome tracking. Broad market discovery,
scheduled reports and autonomous execution are deferred. Trading, payments,
arbitrary shell commands and medical diagnosis are prohibited, even if a
model proposes them.

The requirements originate in the project's `.idea/capstone_proposal.md` and
`.idea/capstone_assessment.md`. The existing
[multi-agent design](multi_agent_design.md) documents a broader configurable
MCP/trading direction. This capstone adopts its useful runtime foundations,
not its trading scope or unrestricted tool configuration. Existing
[security decisions](security_design.md) remain authoritative for current
behavior; this document describes additional controls still to implement.

## 2. Existing foundations versus required work

| Area | Existing foundation | Required capstone delta |
|---|---|---|
| Local application | Streamlit, authentication, encrypted vault, uploads, document categories and chat sessions | One integrated assistant/forecast experience |
| Retrieval | Structured-record lookup and Chroma-based document retrieval | Typed tools with authorization and provenance |
| Agent execution | Agent configuration/store, tool-using runtime, MCP integration | Coordinator delegation, shared execution budgets and hard tool policy |
| Local inference | Ollama and oMLX interfaces selected through central configuration | Reuse interfaces; validate local endpoints and supported tool/schema behavior |
| Forecast research | Public-data adapters, research evidence, heuristic scoring and specialist roles | Extract a small package; fix provenance, validation, concurrency and scoring defects |
| Memory | Encrypted documents, conversation history and agent state | Approved, retrievable task memories with correction and forgetting semantics |
| Outcomes | Stock prediction snapshots and evaluation logic | Explicit consent, immutable evidence cutoffs, correct trading-session evaluation |

Conversation history is not automatically task memory. Existing heuristic
scores are not calibrated probabilities, and existing outcome tracking is
not a leakage-safe historical backtest.

Unfinished LiteLLM changes are preserved separately on
`preserve/litellm-before-capstone-20261018`, not part of this design's initial
inference path. A future proxy requires compatibility and private-prompt
retention review before adoption.

## 3. Component architecture

```text
Browser on localhost
        |
Streamlit UI: login, chat, sources, scenarios, approval controls
        |
Local coordinator + shared bounded run state
        |
Policy gate: scope, schema, data classification, budgets, consent
        |
        +--> Personal Assistant
        |       +--> Authorized structured-record tools
        |       +--> Existing document retrieval
        |       +--> Approved encrypted task memory
        |
        +--> Forecaster
                +--> Fixed public-market provider adapters
                +--> Immutable public evidence snapshot
                +--> Deterministic numerical research tools
                +--> Parallel one-shot specialist reviews
                +--> Deterministic scenario aggregation
                +--> Approved forecast persistence / outcome evaluator
        |
Local result composition: evidence, uncertainty, disagreement, failures
```

The coordinator routes assistant-only, forecast-only or mixed requests. It
does not replace the vault, duplicate authentication or make numerical
forecasts through unconstrained text generation. Pure calculations belong
in deterministic tools; agents choose tools and interpret evidence.

Proposed destination is `src/forecaster/` with explicit imports and a small
public facade. Its domain contracts should not import Streamlit, HTTP route
modules, database sessions or the coordinator. Provider adapters own network
access; encrypted persistence remains owned by `personal_ai`.

Use direct in-process composition initially. A local HTTP bridge would
retain a second application and its write-on-read/background behaviors,
contrary to the chosen consolidation scope.

## 4. Trust boundaries and permissions

The private execution boundary contains decrypted records, chat, approved
memories, local inference and local result composition. The Forecaster's
network boundary receives only validated public query parameters.

| Boundary | Allowed | Not allowed |
|---|---|---|
| Private retrieval | Authorized record references, scoped reads, local summaries | Locked-vault access or widening scope on model request |
| Market requests | Canonical symbol, supported horizon, explicit as-of time, fixed provider options | Private prompts, account details, holdings, document text, arbitrary URLs |
| Specialist inputs | One bounded public evidence snapshot | Private assistant context, authentication state, vault keys |
| Persistent writes | Exact user-approved memory or forecast payload | Automatic persistence because an agent called a write tool |
| Logging | Run/tool IDs, timings, status codes, coverage and non-sensitive metadata | Raw prompts, private arguments, tool payloads, credentials or decrypted records |

Enforce schemas and permissions in code outside model discretion. Reject
unknown fields; validate symbols against provider-supported canonical
identifiers rather than forwarding arbitrary text. Keep provider endpoints
fixed/allowlisted; validate redirects and returned content size/type.

Do not forward coordinator history into public-data adapters or specialists.
A mixed request is decomposed locally into a private assistant task and an
explicit public-symbol task. If safe public parameters cannot be determined,
ask for clarification instead of inferring them from private records.

Existing user-configurable MCP connections are not automatically capstone
tools. The capstone must expose only its allowlisted tools, deny trading and
stdio command execution, and prevent private context reaching remote MCP.
A warning banner or model instruction alone is not enforcement.

Preserve localhost binding and the existing AuthManager/key lifecycle.
Chroma document text and metadata remain plaintext on disk under the
documented restrictions. Do not put new private task memories into that
index; decrypt authorized records for bounded in-memory retrieval initially.

## 5. Run lifecycle and bounded reasoning

1. Authenticate, classify the request and validate its scope.
2. Create shared run state and remaining budgets; select a bounded plan.
3. Execute permitted actions, inspect observations and revise the plan.
4. Compare evidence or scenarios; clarify, abstain or finish.
5. Compose the grounded result; request approval for any proposed writes.

Use a bounded ReAct-style action/observation loop with at most five
orchestration rounds and one retry per failing logical tool operation.
All delegated work and retries consume a shared run-wide tool-call budget,
model-call budget and wall-clock deadline. A round cannot evade these
limits by requesting many calls. Configure budgets centrally in
`src/config.py`; child agents cannot reset or increase them.

Specialists are one-shot workers, not recursively spawning agents. Cancel
outstanding work at the deadline and expose incomplete results. Retry only
classified transient failures; do not retry denied actions, invalid
arguments or missing evidence as if they were transient errors.

An approval pause suspends execution. Resume only the approved payload
after rechecking authorization and run/evidence validity; expiry requires
fresh confirmation. Never hold a network task open while awaiting approval.

Expose selected actions, evidence references and concise decision rationales,
not private chain-of-thought. Distinguish `completed`, `partial`,
`needs_input`, `awaiting_approval`, `abstained` and `failed`; empty evidence
must not masquerade as success.

## 6. Data contracts and ownership

| Contract | Essential fields / invariants |
|---|---|
| Run state | Run ID, authorized scope, status, deadline, remaining budgets and tool events |
| Tool result | Tool/call ID, typed payload or explicit error, sources, observation time and completeness |
| Evidence snapshot | Snapshot ID, content hash, source/provider, event/publication/retrieval/availability times, as-of cutoff, coverage and licensing notes |
| Specialist review | Role, snapshot ID, supported claims/source IDs, scenario, risks, uncertainty and validation status |
| Forecast | Symbol, model/scorer version, snapshot ID, origin time, exchange calendar, 5/21-session horizon, target definition and heuristic outputs |
| Task memory | Approved content, kind, source references, creation/expiry time, superseded-by reference and approval provenance |
| Realized outcome | Forecast ID, target session, actual observation/session time, price convention, benchmark and evaluation status |

Validate contracts locally, including model-generated JSON. Schema hints at
the inference endpoint are not sufficient. Reject invented source IDs,
non-finite values and unsupported horizons; do not fill missing model fields
with plausible opinions.

Freeze a bounded public evidence packet before parallel specialist work.
Each specialist receives the same immutable snapshot ID and content.
Aggregate valid reviews deterministically; report disagreement, missing roles
and reduced coverage. A failed specialist must not erase successful peers
or silently vanish.

Private task memory and approved forecast records use existing encrypted
vault facilities, without a second key path. Exclude internal records from
document views and general RAG indexing. Public provider caches contain
public data only and have explicit retention limits.

## 7. Representative workflows

**Document-backed assistance:** find authorized statements, retrieve exact
structured amounts, calculate totals deterministically and cite records.
Missing statements produce an incomplete-period warning, not invented data.
An approved correction supersedes the old memory rather than creating two
equally authoritative facts.

**Requested-symbol research:** validate the request, gather dated public
evidence, record provider coverage, compute heuristic signals and freeze the
snapshot. Run independent macro, valuation, event and downside specialists
concurrently. Compare bullish/base/bearish alternatives against explicit
evidence/risk criteria; report uncertainty and disagreement. Saving the
forecast requires approval.

**Mixed task:** summarize recorded obligations privately and research the
user-specified symbol separately. Combine outputs locally while keeping
obligations and speculative estimates distinct. Do not infer investment
suitability or transmit the private financial summary to a provider.

**Outcome evaluation:** after an approved forecast matures, resolve its
target exchange session and obtain the corresponding price under the
declared adjustment convention. Missing target-session data stays pending
or unavailable; never substitute today's quote. Persist evaluated outcomes
only through the approved write policy.

## 8. Forecast correctness and evaluation

Reuse research contracts, source registry, event/theme logic, provider
adapters, scoring, specialist roles and outcome concepts selectively.
Do not copy the eager service initializer, legacy application lifecycle,
SQLAlchemy persistence or scheduled report writer.

Before migration, add regressions for inspected risks: wrong target-date
pricing, naive timezone comparisons, incorrect fallback-provider provenance,
adverse events increasing optimistic scores, unvalidated specialist output
and lost partial results.

Normalize timestamps to aware UTC; retain the exchange calendar for session
boundaries. Define whether each horizon starts at the current or next
eligible close, including holidays and after-hours requests, before creating
a forecast. Version the definition with each snapshot.

Historical evaluation requires point-in-time availability, not merely a
publication date. Latest SEC amendments or revised FRED observations cannot
be treated as information available at an earlier cutoff. Until historical
provider adapters support this, evaluate prospective frozen forecasts and
label historical backtesting unsupported.

Compare 5- and 21-session results separately against predeclared simple
baselines and a market benchmark on identical chronological samples. Report
sample size, directional accuracy, return-estimate error, coverage,
abstention and uncertainty. Evaluate calibration only for defensible
probabilistic outputs; label current scores uncalibrated. Include transaction
costs if reporting simulated investment performance.

## 9. Delivery sequence and acceptance gates

| Stage | Deliverable | Gate |
|---|---|---|
| Foundation | Preserve local work, sync main, retain original files | No work/data loss; existing behavior retained |
| Policy and contracts | Typed tools, private/public boundary, shared run budgets | Invalid arguments and unauthorized actions cannot execute |
| Forecast extraction | Minimal contracts, providers and deterministic scoring | Offline fixtures prove provenance, errors and supported horizons |
| Coordination | Coordinator, two core agents, parallel specialists | Mixed task uses both agents; timestamps prove overlap; failures remain visible |
| Memory and outcomes | Approved encrypted records, corrections and session-aware evaluation | No unapproved writes, forgotten-memory retrieval or substitute-date outcomes |
| Capstone evidence | Reproducible task set and leakage-aware forecast comparison | Report measured results, limitations and unmet targets |

Initial acceptance targets from the assessment: correct evidence-backed tool
sequences on at least 18/20 labeled tasks; recovery on at least 9/10
recoverable injected failures; retrieval of at least 9/10 seeded approved
memories. Every failure case must terminate within budgets. Adversarial cases
must produce zero unauthorized actions. These are targets, not results.

Use mocked providers/local-model doubles and synthetic records for regression
tests. Exercise locked access, injected document instructions, malformed
schemas, timeouts, partial specialist failure, correction/forgetting,
unapproved writes and attempted private-data egress. Keep tests isolated from
real HOME, vault, `.env`, databases and live market services.

Before implementation delivery, run the smallest relevant existing tests,
then broader affected suites, syntax checks, `make check-context` for
chat/RAG changes and isolated Streamlit AppTest flows. Inspect validation and
CI definitions before execution or publishing. A green suite does not prove
absence of bugs or profitable predictions.

## 10. Decisions retained for implementation

- Reuse Ollama/oMLX through central configuration; do not replay preserved
  LiteLLM work.
- Use one application and physical package extraction, not a permanent API
  bridge or wholesale repository copy.
- Start with requested-symbol research, scenarios and outcomes; defer market
  discovery and scheduled reporting.
- Require user approval for persistence; explicitly design consent scope,
  idempotency and concurrent vault access before implementation.
- Confirm provider terms, redistribution permissions and point-in-time data
  availability before selecting final data sources.
- Keep the original projects and committed history recoverable. Cleanup
  removes only individually approved redundant paths, never real user data.
