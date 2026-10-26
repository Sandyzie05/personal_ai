# Documentation index

Start here. Each doc has one job; where two overlap, the pointer says which one is authoritative.

| I want to... | Read |
|---|---|
| Understand the system in 10 minutes | [architecture.md](architecture.md) |
| Know the rules, current status and what to build next (also the brief for a coding agent) | [platform_plan.md](platform_plan.md) |
| Understand or improve document retrieval (RAG), bill ingestion, freshness | [rag_design.md](rag_design.md) |
| Work on local models, embeddings, oMLX, re-indexing | [ai_engine.md](ai_engine.md) |
| Work on stock research, recommendations, outcome tracking | [forecasting_system.md](forecasting_system.md) |
| Work on the agent loop, MCP servers, skills, confirmation gate | [multi_agent_design.md](multi_agent_design.md) |
| Check security design, threat model, residual risks, audit log | [security_design.md](security_design.md) |
| **Test the system by hand (RAG, agents, predictions)** | [user_testing_guide.md](user_testing_guide.md) |
| See how the capstone feedback was addressed | [capstone_feedback_response.md](capstone_feedback_response.md) |

Related files outside `docs/`: `../README.md` (setup and features), `../AGENTS.md` (repo rules for coding agents), `../CHANGELOG.md` (history),
`../.idea/` (capstone submissions), `../evals/` (`make eval`), Miro board: https://miro.com/app/board/uXjVEVwdVZo=/

## Suggested reading order

1. **New to the project:** architecture → platform_plan §0-§2 → security_design "At a glance".
2. **Fixing answer quality:** rag_design §1-§3 → ai_engine → user_testing_guide Part A.
3. **Building agents/skills:** platform_plan §3 and §7 (runbook) → multi_agent_design §5, §8.
4. **Reviewing safety:** security_design → platform_plan §2 (invariants) and §6.2 (audit).

## Conventions

- Status words: **DONE** (code, tests, eval), **PARTIAL**, **OPEN**. The source of truth for status is [platform_plan.md](platform_plan.md) §6.
- Every security-relevant change updates the log in security_design.md and the invariants table in platform_plan.md in the same change.
- Section numbers in `multi_agent_design.md` are referenced from source comments; do not renumber them.
