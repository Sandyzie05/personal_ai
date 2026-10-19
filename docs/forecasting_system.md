# Personal AI Forecasting System

## Purpose and Scope

The forecasting system enables the Personal AI to research requested stock symbols, compare evidence-backed scenarios, and evaluate forecasts after their horizons have elapsed. It provides a structured approach to market research with explicit evidence collection and outcome tracking.

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
5. **Outcome Tracking**: Save forecasts and evaluate after horizons

## Data Contracts and Ownership

### Key Data Types
- **Run state**: Execution context and budgets
- **Tool results**: Typed payloads with source information
- **Evidence snapshots**: Immutable public data packets
- **Specialist reviews**: Role-based analysis with risk assessment
- **Forecasts**: Symbol-specific predictions with horizons
- **Task memory**: Approved content with approval provenance
- **Outcomes**: Evaluated forecasts with actual results

### Trust Boundaries
- Private execution boundary: Decrypted records, chat, approved memories
- Public research boundary: Only validated public query parameters
- Strict data validation for all external inputs
- Immutable evidence for specialist review

## Forecast Evaluation

### Correctness Requirements
- Historical evaluation requires point-in-time availability
- Normalized timestamps to UTC with exchange calendar support
- Versioned snapshot definitions with explicit timing rules
- Separate evaluation of 5- and 21-session horizons

### Performance Metrics
- Directional accuracy comparison
- Return-estimate error measurement
- Coverage and abstention rates
- Uncertainty and disagreement reporting

## Implementation Design

### Run Lifecycle
1. Authentication and scope validation
2. Shared run state and budget creation
3. Execution of permitted actions
4. Evidence comparison and decision making
5. Result composition with user approval

### Bounded Reasoning
- Maximum 5 orchestration rounds
- Shared tool-call and model-call budgets
- Wall-clock deadline enforcement
- Retry only classified transient failures

### Security Requirements
- All research and outcomes in encrypted vault
- No unauthorized data egress from agents
- Explicit user approval for any persistence
- All tool calls logged for audit purposes