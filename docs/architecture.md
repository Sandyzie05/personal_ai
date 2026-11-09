# Architecture

personal_ai is a local, privacy-first personal assistant and forecaster. Everything private (documents, chat, memory, credentials) stays on
your machine, encrypted at rest in one vault. Models run locally on **oMLX**. A **Coordinator** delegates work to specialist agents and
only hands you a result when the evidence supports it; otherwise it tells you what is missing, stale or in conflict.

Reading order: this file → [platform_plan.md](platform_plan.md) (status, rules, backlog) → [rag_design.md](rag_design.md) →
[security_design.md](security_design.md). Index: [README.md](README.md).

## 1. Layers

```
 UI            Streamlit (localhost only): Chat, Upload, Files, Dashboard, Agents, Forecast, Settings
 Agents        Coordinator → specialists (Expense, Market, Forecaster, Verifier rule) over a typed message bus;
               untrusted input quarantine; approval-gated Core Memory; scheduler; MCP tools (read-only by default)
 AI engine     oMLX chat + embeddings (OpenAI-compatible, loopback) · RAG (ChromaDB) · structured-record path
 Data          Encrypted vault (AES-256-GCM, key from your password) · ingestion + extraction · public market providers
 Security      Password gate, single key path (AuthManager), allowlisted tools, schema-checked agent messages, audit log
```

| Layer | Code | Detail doc |
|---|---|---|
| UI | `src/interface/` | [platform_plan.md](platform_plan.md) §3.1 |
| Agents | `src/agents/` (`orchestration/` = coordinator, bus, quarantine, memory, policy) | [platform_plan.md](platform_plan.md), [multi_agent_design.md](multi_agent_design.md) |
| AI engine | `src/ai_engine/` | [ai_engine.md](ai_engine.md), [rag_design.md](rag_design.md) |
| Forecaster | `src/forecaster/` | [forecasting_system.md](forecasting_system.md) |
| Data + security | `src/data_vault/`, `src/security/`, `src/data_ingestion/`, `src/data_extraction/` | [security_design.md](security_design.md) |
| Evals | `evals/` (`make eval`) | [platform_plan.md](platform_plan.md) §5 |

## 2. How a question flows

1. Your message goes through a **request policy** (trading, payments, shell commands, diagnosis and secret requests are refused).
2. A rule-based **planner** turns it into steps (bills → Expense; tickers → Forecaster per symbol; market words → Market).
3. Independent steps run **in parallel**; each has a timeout and bounded retries. Specialists return typed **findings** (value, evidence ids, confidence, status).
4. A deterministic **merge** groups findings, reports conflicts instead of averaging them, and an **escalation** step lists what needs you
   (conflict, stale/unavailable data, failed step, no evidence cited).
5. The reply is rendered with sources. Anything worth remembering becomes a **proposal**; only your click in *Agents → Core Memory* saves it.

Document questions use the retrieval stack in [rag_design.md](rag_design.md): exact figures come from structured extracted records,
fuzzy questions from semantic search over your PDFs.

## 3. Design principles

- **Local first.** Private content only goes to local services. Public market research sends tickers/topics only, never personal context.
- **One key path.** The vault key exists only via `AuthManager`; there is no second way to read data.
- **Untrusted by default.** Email, portal, web and news text is data, never instructions; it reaches agents only as schema-checked facts.
- **Least disclosure.** Agents exchange only the fields a task needs, over typed channels.
- **Deterministic where it matters.** Totals, merging and escalation are code; models interpret, they do not have the last word on a number.
- **Explicit uncertainty.** Missing, stale or conflicting evidence becomes a visible status and a question to you, not a confident summary.
- **Human approval for persistence.** Core Memory changes and any write-capable tool need you.

## 4. What changed from the original plan

| Original idea | What exists |
|---|---|
| Ollama + LlamaIndex | oMLX (Ollama kept but disabled by default), hand-rolled RAG on ChromaDB |
| SQLCipher | Application-level AES-256-GCM over SQLite |
| Biometric login | Password-derived key |
| Single assistant | Coordinator + specialist agents, MCP tools, skills |
| Not planned | Forecaster (public evidence, specialists, outcome tracking), eval harness, quarantine, scoped message bus |
| Still open | Email/portal ingestion wiring, key rotation/backup, voice, cross-device sync, file watcher (see [platform_plan.md](platform_plan.md) §6.1) |

## 5. Known limits

The ChromaDB index is plaintext on disk (locked down, documented in [security_design.md](security_design.md)). Forecasts are uncalibrated
heuristics. If you forget your password there is no recovery. oMLX tool calling is verified with fakes only until it is run against a live server.
