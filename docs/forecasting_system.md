# Personal AI Forecasting System

## Purpose and Scope

The forecasting system enables the Personal AI to research requested stock symbols, compare evidence-backed scenarios, and evaluate forecasts after their horizons have elapsed. It provides a structured approach to market research with explicit evidence collection and outcome tracking.

Status (2026-10-19): requested-public-US-ticker research, optional parallel
specialists and explicit encrypted forecast/outcome saves are implemented.
General coordination and approved task memory are proposed separately in
[agentic_system_design.md](agentic_system_design.md). Generic MCP agent
permissions are not inherited by this workflow.

## Core Components

### Research Framework
- Public-data adapters for financial research
- Evidence collection with timestamped snapshots
- Heuristic scoring and specialist roles
- Parallel one-shot specialist reviews

### Forecast Process
1. **Symbol Research**: Validate requested symbol, gather dated public evidence
2. **Evidence Snapshot**: Freeze immutable public evidence packet for specialist review
3. **Specialist Reviews**: Run parallel one-shot specialist reviews
4. **Scenario Aggregation**: Combine reviews deterministically
5. **Outcome Tracking**: Preview/approve an immutable forecast; explicitly
   evaluate and separately approve saving an outcome after its horizon

## Data Contracts and Ownership

### Key Data Types
- **Research results**: Public evidence, provider failures and coverage
- **Evidence snapshots**: Immutable public data packets
- **Specialist reviews**: Role-based analysis with risk assessment
- **Forecasts**: Symbol-specific predictions with horizons
- **Outcomes**: Evaluated forecasts with actual results

Curated task memory and a shared coordinator run state are future contracts,
not records currently produced by this page.

### Trust Boundaries
- Private execution boundary: Forecast UI and explicitly encrypted saved records
- Public research boundary: Only validated public ticker/provider parameters;
  no vault documents, holdings, account identifiers or chat history
- Strict data validation for all external inputs
- Immutable evidence for specialist review

## Forecast Evaluation

### Correctness Requirements
- Leakage-safe historical evaluation requires point-in-time availability;
  current adapters cannot reconstruct historical SEC/FRED data vintages
- Normalized timestamps and caller-attested exchange-session sequences;
  no independent official-calendar verification
- Versioned snapshot definitions with explicit timing rules
- Separate evaluation of 5- and 21-session horizons

### Performance Metrics
- Directional accuracy comparison
- Return-estimate error measurement
- Coverage and abstention rates
- Uncertainty and disagreement reporting

These are evaluation dimensions, not measured accuracy claims. Current
scores are uncalibrated heuristics, not probabilities, profitability
evidence or investment advice.

## Implementation Design

### Run Lifecycle
1. Unlock the existing vault and open Forecast.
2. Enter a public US ticker and choose 5-/21-trading-session horizons.
3. For trackable forecasts, supply and attest a complete official session
   sequence before **Research ticker**. Without it research is unscheduled.
4. Review dated evidence, coverage and provider failures; optionally run
   local specialists on the public snapshot.
5. **Prepare forecast** previews the exact captured record; **Save forecast**
   approves only that content and writes it encrypted, insert-once.
6. Supply explicit source-attributed origin/target session closing prices
   for symbol and benchmark, plus an aware evaluation timestamp.
7. **Evaluate outcome** displays results; **Save outcome** is a separate
   approval. Opening or evaluating records does not implicitly save them.

Changed preview payloads invalidate approval. Missing session prices never
fall back to the latest quote. There is no automatic calendar fetch,
scheduled evaluator, live-news ingestion or historical backtest.

### Bounded Reasoning
- Parallel one-shot role reviews with finite model-call budget and limited
  transport retry; not a five-round general coordinator
- Research and specialist time budgets defined in `src/config.py`
- Specialist deadlines stop waiting, but cannot forcibly terminate active
  synchronous HTTP calls; transport timeouts are inactivity bounds
- Invalid model evidence references are rejected; failures stay visible

### Security Requirements
- Research/scenario previews remain session-local until explicit Save
- Saved forecasts/outcomes use the existing AuthManager DEK and internal
  vault prefixes excluded from document retrieval
- Public providers use fixed adapter operations, not arbitrary model URLs
- Specialist inference is restricted to loopback endpoints, with redirects
  and environment proxies disabled for this path
- Explicit user approval for any persistence
- No claim of unified audit coverage for every provider/model/tool call

See [security_design.md](security_design.md) for precise endpoint, deadline
and persistence boundaries, and
[development_planning.md](development_planning.md) for the integrated build
sequence.