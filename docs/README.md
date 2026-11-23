# Documentation index

Each doc has one job. Status lives in one place: [roadmap.md](roadmap.md) §1.

| I want to... | Read |
|---|---|
| Understand the system in 10 minutes (diagrams, user flow) | [architecture.md](architecture.md) |
| **Know what is left to build, card by card (brief for a coding agent)** | [roadmap.md](roadmap.md) |
| Know the rules, invariants, contracts, config and measured results | [platform_plan.md](platform_plan.md) |
| Understand or improve document retrieval (RAG) and bill ingestion (PDF, Gmail email text R11, portals) | [rag_design.md](rag_design.md) |
| Work on local models, embeddings, oMLX, memory budget | [ai_engine.md](ai_engine.md) |
| Work on stock research, Tree of Thought, outcome tracking | [forecaster.md](forecaster.md) |
| Work on the Trader tab (plan + AI paper agents on simulated money) | [trader.md](trader.md) |
| Work on the agent loop, MCP servers, skills, confirmation gate | [multi_agent_design.md](multi_agent_design.md) |
| Check security design, threat model, residual risks, audit | [security_design.md](security_design.md) |
| Trace runs, metrics, logs and guardrail dashboards (OpenTelemetry, local Grafana) | [observability.md](observability.md) |
| Test the system by hand (RAG, agents, predictions) | [user_testing_guide.md](user_testing_guide.md) |
| Report the model laptop's state back to the dev session (redacted, paste only) | [state_report.md](state_report.md) |

Outside `docs/`: `../README.md` (setup), `../AGENTS.md` (repo rules for coding agents), `../CHANGELOG.md` (history),
`../evals/` (`make eval`), Miro board https://miro.com/app/board/uXjVEVwdVZo=/. Course submissions and feedback are kept locally in `../.idea/` (not committed).

## Reading order

1. **New session / new to the project:** roadmap §00 (start here) → architecture → platform_plan §0-§2 → roadmap §1-§3.
2. **Picking up a card:** `AGENTS.md` → roadmap §3 (conventions) → the card → the design doc it names.
3. **Fixing answer quality:** rag_design §3 → ai_engine → user_testing_guide Part A.
4. **Reviewing safety:** security_design → platform_plan §2 (invariants) → roadmap P4.

## Conventions

- Status words: **DONE** (code, tests, eval), **PARTIAL**, **PLANNED**, **USER** (needs a live run on the user's laptop).
- Every security-relevant change updates `security_design.md` and the invariants table in `platform_plan.md` §2 in the same change.
- Section numbers in `multi_agent_design.md` are referenced from source comments; do not renumber them.
