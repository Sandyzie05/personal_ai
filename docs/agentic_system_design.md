# Integrated Assistant and Forecaster Design

Status: proposed integration, assessed 2026-10-19. Existing foundations are
implemented; coordinator, research-only policy and approved task memory are
not. Build order and measurable gates:
[development_planning.md](development_planning.md).

## Product boundary

The Week 1 capstone combines cited personal assistance with requested-stock
research while retaining user control over private data and decisions.
Week 2's Gmail, portal, schedule and broader automation ideas are future
work, not current capabilities. Historical inputs remain under `.idea/`.

Use the existing single-user localhost Streamlit app, AuthManager and
DataVault. Do not add a second key path, backend service, generic shell tool
or agent framework merely to connect working modules.

## Foundations to reuse

| Existing module | Responsibility |
|---|---|
| `security/auth.py`, `data_vault/` | Password-wrapped DEK and encrypted records |
| `ai_engine/chat_engine.py`, `data_extraction/` | Scoped document evidence, structured records and grounded answers |
| `agents/runtime.py`, `agents/mcp.py` | Configurable user-selected MCP tool loops and confirmation pauses |
| `forecaster/research.py`, `forecaster/scenarios.py` | Public snapshots and bounded parallel specialist reviews |
| `forecaster/outcomes.py`, `interface/forecast.py` | Explicit session/price evaluation and content-bound immutable saves |

Generic MCP agents can expose user-configured actions, including trading.
The new capstone workflow must not inherit those permissions automatically.
Public-only forecasting is already a separate path; it does not impose a
universal restriction on generic agents.

## Proposed execution architecture

```text
Authenticated user
    -> typed task + explicit scope
    -> bounded coordinator
        -> private document/structured-record operations
        -> public research operation (validated public fields only)
        -> public-only specialist review
    -> locally composed answer with evidence, failures and limitations
    -> exact-content preview
    -> explicit user approval
    -> encrypted task memory / forecast / outcome
```

Private text, chat history, obligations, holdings, identifiers and memory
never become free-form provider arguments. Public research receives a
validated ticker and allowed public parameters. Keep the public snapshot
schema independent of private task state; compose mixed answers locally.

## Typed operations and authorization

Each operation needs a validated input/output schema, evidence identifiers,
failure status, permission requirement and budget charge. Begin with
retrieval, structured-record lookup, deterministic aggregation, public
research and specialist review. Unknown operations, arbitrary URLs,
commands, trades and payments fail closed.

Permissions derive from user scope, not model instructions. Child work
inherits a subset of parent permissions and consumes the same budgets.
Uploaded text, skill files, remote results and model output are untrusted
data, never authorization. A user-selected MCP server is not proof that its
tool annotations are accurate.

Approval must bind operation, run, record identity and exact canonical
payload digest. Editing a payload invalidates approval. The forecast
preview/save flow provides existing prior art; do not replace it with a
model-generated statement that the user approved.

## Bounded coordination

Proposed run states: running, awaiting approval, completed, partial,
abstained, cancelled and failed. Record evidence and visible failure causes
without raw private arguments in routine diagnostics.

Limit the coordinator to five steps with shared model/tool/fan-out budgets.
Permit one retry only for classified transient failures. Recursive
delegation cannot reset limits. Stop at deadline and report incomplete
evidence; synchronous transport calls may outlive the wait deadline, so
transport timeouts and cancellation limitations must remain explicit.

## Approved encrypted memory

Proposed memory records contain kind, content, provenance, approval identity,
creation/expiry time and supersession links. Support preferences,
corrections, task summaries and outcomes. Build preview/approval,
authorization-aware retrieval, expiry, correction and deletion before any
automatic extraction.

Retrieve these records in memory after authorized decryption. Do not index
private task memory into plaintext Chroma. Conversation history, activity
logs and paused agent runs are existing persisted state, not substitutes
for curated approved memory.

## Evaluation

Use deterministic synthetic fixtures and fake transports first. Tests must
assert actual tool sequences, terminal states, citations, shared budget
limits and privacy canaries, not merely object shape.

Targets: correct evidence-bearing sequence on at least 18/20 labeled tasks,
recovery on at least 9/10 labeled recoverable failures, appropriate retrieval
of at least 9/10 seeded memories, and zero forbidden actions/private-canary
egress in the adversarial set. These are unmeasured acceptance targets.

Evaluate 5- and 21-session forecasts separately. Current heuristic scores
are uncalibrated; current provider adapters cannot reconstruct point-in-time
historical vintages. Manual calendar attestation and supplied closing-price
provenance are not independently verified market data. Do not claim
profitability, investment suitability or leakage-safe historical backtests.

## Security and release boundary

The primary vault is encrypted, but Chroma text/metadata and user-written
skills are plaintext residual risks. Configured inference/MCP endpoints
define real egress; localhost UI binding is not an egress policy. See
[security_design.md](security_design.md) for current risks, including
ingestion and logging boundaries.

Ship the foundation fixes before integration. A release requires isolated
regressions, reviewed security findings, current docs and actual check
results. No automation, credential acquisition, deployment or trading is
authorized by this design.
