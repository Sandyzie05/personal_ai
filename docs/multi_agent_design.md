# Multi-Agent Design — Personal AI System

This is the design for turning the Personal AI System from a single
RAG-grounded chat assistant into a **configurable multi-agent system** where
the user defines agents (model + instructions + tools + skills + data scope),
picks one in the chat UI, and the agent runs a tool-using loop backed by
**Model Context Protocol (MCP)** servers — starting with the Robinhood
trading platform.

It is written against the "Future Architecture of AI Agents" reference
diagram (inputs → orchestration layer → agent core → knowledge layer →
outputs → service layer → governance). Every element of that diagram is
mapped in §4 to: what exists today, what **v1 (this build)** adds, and what
is explicitly deferred with the interface that will carry it in v2.

Status (2026-10-19): **configurable agents and MCP tool loops implemented;
automatic coordination, approved task memory and scheduling proposed.**
The dated v1 decisions below preserve design history. The current inference
factory supports Ollama and oMLX; model tool support still requires runtime
verification. This generic agent platform is broader than the research-only
capstone in [agentic_system_design.md](agentic_system_design.md).

---

## 1. Goals and non-goals

### Goals

1. **User-defined agents.** An agent is a named configuration: model,
   temperature, context window, system instructions, RAG scope, MCP servers
   (tools), skills, safety options. Created/edited/deleted in the UI.
2. **Agent selection in chat.** One selector in the Chat page routes each
   message to the chosen agent.
3. **Real tool use via MCP.** The agent's model can call tools exposed by
   MCP servers (remote HTTP or local stdio), see their results, and reason
   over them — including the Robinhood trading MCP
   (`https://agent.robinhood.com/mcp/trading`).
4. **Safety for actions.** With the agent's confirmation setting enabled,
   tools classified as mutating pause for in-UI approval. Classification and
   optional confirmation are not a universal authorization policy.
5. **Keep the security posture.** All new state (agent configs, MCP
   configs, OAuth tokens, activity log, run state) lives in the encrypted
   vault. No new plaintext-at-rest location except skill instruction files
   (user-controlled plaintext, documented in §9).
6. **Testable without a browser.** Core logic (store, tool mapping, agent
   loop) is pure-Python and unit-tested with fakes; UI is smoke-tested with
   `streamlit.testing.v1.AppTest`.

### Non-goals (v1)

- No automatic agent-to-agent handoff / planner that dispatches sub-agents
  (v2 — the runtime is shaped to support it, §10).
- The original v1 selected Ollama only. The current application also has
  oMLX clients; this historical constraint is no longer a backend inventory.
  Configurable endpoints must be kept local/trusted for private inference.
- No image/audio/video inputs, scheduled/autonomous agents, long-term
  memory extraction, knowledge graph, or visualization generation from
  agents. All mapped to the roadmap (§4, §10).

---

## 2. What exists today

| Piece | File | Role |
|---|---|---|
| Login gate + DEK lifecycle | `src/security/auth.py` | Password → wrapped DEK; DEK only in `st.session_state` |
| Encrypted vault | `src/data_vault/` | AES-256-GCM values in SQLite at `~/.personal_ai_vault` |
| RAG | `src/ai_engine/rag_engine.py`, `chroma_store.py` | ChromaDB index at `<vault>/.chroma` (plaintext, locked down — see `docs/security_design.md`) |
| Chat engine | `src/ai_engine/chat_engine.py` | Query → (structured records \| category-scoped RAG \| plain RAG) → Ollama prompt; budgeted history; streaming |
| Ollama client | `src/ai_engine/ollama_client.py` | Thin wrapper; `num_ctx`, temperature, structured output |
| Sessions | `src/interface/chat_sessions.py` | N chat sessions in vault key `chat_sessions_index` |
| UI | `src/interface/main.py` + pages | Chat / Upload / Files / Dashboard / Settings |

Key facts verified on this machine (2026-10-10):

- Ollama (localhost:11434) serves tool-capable models:
  `qwen3.8:27b-mlx`, `gemma4:31b-mlx`, `gpt-oss:20b`, `ornith-1.5:35b`
  (all advertise `capabilities: ["...","tools","..."]` via `/api/tags`).
- Ollama's `/api/chat` does **native function calling**: accepts
  `tools: [...]`, returns `message.tool_calls`, and completes the loop when
  `role: "tool"` result messages are appended (verified end-to-end with
  `qwen3.8:27b-mlx`, including in streaming mode — `tool_calls` arrive in a
  pre-`done` chunk).
- The Robinhood MCP is a **remote streamable-HTTP MCP server** requiring
  **OAuth 2.1 authorization-code + PKCE** (public client, dynamic client
  registration, scope `internal`, no device-code grant — a browser flow is
  mandatory). Discovered metadata:
  - Protected-resource: `https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading`
  - Authorization endpoint: `https://robinhood.com/oauth`
  - Token endpoint: `https://api.robinhood.com/oauth2/token/`
  - Registration endpoint: `https://agent.robinhood.com/oauth/trading/register`
- The `mcp` Python SDK (v2.3.0) implements the full flow: metadata
  discovery, dynamic registration, PKCE, refresh — the app only supplies a
  `TokenStorage`, a `redirect_handler` (open browser), and a
  `callback_handler` (return `AuthorizationCodeResult`).

---

## 3. Target architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  INPUTS                                                                     │
│  Text (chat)          Structured data (vault docs)      Images v2          │
└──────────┬──────────────────────────┬──────────────────────────────────────┘
           │                          │
┌──────────▼──────────────────────────▼──────────────────────────────────────┐
│  AGENT ORCHESTRATION LAYER                                                 │
│  Agent Registry (vault: agents_index)        User Routing (chat selector)  │
│  per-session agent binding                          v2: planner dispatch   │
└──────────┬─────────────────────────────────────────────────────────────────┘
           │
┌──────────▼─────────────────────────────────────────────────────────────────┐
│  AI AGENT CORE  (src/agents/runtime.py)                                    │
│                                                                            │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────────────────┐   │
│  │ Planning &   │  │ Memory & State   │  │ Tool Use & Execution        │   │
│  │ Reasoning:   │  │ system prompt +  │  │ Ollama native tool calling  │   │
│  │ tool loop    │  │ instructions +   │  │ ⇄ McpManager ⇄ MCP servers  │   │
│  │ w/ model     │  │ skills + RAG     │  │ confirmation gate for       │   │
│  │ (prompt eng) │  │ context, budgeted│  │ sensitive tools             │   │
│  └──────────────┘  │ history (vault)  │  └─────────────────────────────┘   │
│                    └──────────────────┘  Self-learning: v2 (feedback)      │
└──────────┬─────────────────────────────────────────────────────────────────┘
           │                                    │
┌──────────▼─────────────────┐     ┌────────────▼───────────────────────────┐
│  KNOWLEDGE & DATA LAYER    │     │  SERVICE LAYER                          │
│  Vector DB: ChromaDB       │     │  MCP servers (vault: mcp_servers)       │
│  Document store: vault     │     │   remote: streamable HTTP + OAuth 2.1   │
│  Long-term memory: vault   │     │   stdio: local command                  │
│  (sessions, configs,      │     │  Auth tokens: vault (encrypted)          │
│   activity log)           │     │  v2: other API integrations, channels    │
└────────────────────────────┘     └─────────────────────────────────────────┘
           │                                    │
┌──────────▼────────────────────────────────────▼────────────────────────────┐
│  OUTPUTS                                                                    │
│  Text (chat stream)   Actions (MCP tool calls — trades, alerts)            │
│  Dashboards (existing)   Alerts v2 (agent-created, surfaced in UI)          │
└────────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────────┐
│  GOVERNANCE & FOUNDATION                                                    │
│  Safety & Alignment: grounding prompts, confirmation gate, per-agent scope │
│  Security & Privacy:  localhost-only app, vault-encrypted configs/tokens   │
│  Ethics: "not financial advice" stance in default agent templates          │
│  Monitoring: encrypted activity log (digest, outcome, duration)            │
│  Evaluation & Feedback: v2 (per-answer feedback button, loop into tuning)  │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Element-by-element mapping (reference diagram → system)

| # | Diagram element | Today | v1 (this build) | Roadmap |
|---|---|---|---|---|
| 1 | Inputs → Text | Chat page | Chat page, routed to selected agent | — |
| 2 | Inputs → Images | — | — | v2: vision models (`qwen3.8`, `gemma4` already vision-capable locally); `st.file_uploader` in chat → base64 in Ollama message |
| 3 | Inputs → Audio / Video | — | — | v3: local whisper transcription into text input |
| 4 | Inputs → Structured Data | Vault docs + structured extraction | Reused unchanged as agent context source; per-agent category scoping | — |
| 5 | Inputs → Streams | RAG retrieval | RAG retrieval per agent | Live data feeds as MCP servers |
| 6 | Orchestration → Task Decomposition | — | — | v2: planner agent with an internal `dispatch_agent` tool |
| 7 | Orchestration → Multi-Agent Coordination | Single assistant | **Agent registry** + user-selected routing; per-session agent binding | v2: planner auto-routes; parallel sub-agent runs |
| 8 | Orchestration → Inter-Agent Communication | — | Shared vault (configs, activity log) is the substrate | v2: `agent_handoff_<session>` records; agents can read prior agent's summary in the session |
| 9 | Core → Planning & Reasoning | Prompt construction in `ChatEngine` | **Tool loop** in `AgentRuntime`: LLM → tool_calls → results → LLM (bounded rounds, budgeted) | Replanning on tool failure (v2) |
| 10 | Core → Memory & State | Session history in vault | Agent instructions + skills + RAG context + budgeted history; **run state** persisted for confirmation pauses | v2: long-term memory (extracted facts → vault, injected per agent) |
| 11 | Core → Self-Learning & Reflection | — | — | v2: feedback button stores rating + note per answer; "reflection" agent summarizes patterns |
| 12 | Core → Tool Use & Execution | — | **McpManager** (stdio + remote MCP) + Ollama native function calling + confirmation gate | Internal tools (dispatch, memory write) in v2 |
| 13 | Knowledge → Vector DB | ChromaDB at `<vault>/.chroma` | Shared index; per-agent `where` scope by category | Per-agent indexes v2 if needed |
| 14 | Knowledge → Knowledge Graph | — | — | v3 |
| 15 | Knowledge → Document Store | Encrypted vault | Agent/MCP/skill-adjacent state also in vault (schemas §6) | — |
| 16 | Knowledge → Long-term Memory | — | Encrypted activity log, not curated task memory or complete audit coverage | v2: curated memory |
| 17 | Outputs → Text / Reports | Chat | Chat, streamed, with source citations + tool-use trail | Markdown export v2 |
| 18 | Outputs → Visualizations | — | Tool results rendered as text/tables in chat | v2: chart tool (plotly → data URI) |
| 19 | Outputs → Dashboards | Dashboard page (vault data) | Agent activity is shown on the Agents page, not a dashboard widget | — |
| 20 | Outputs → Actions | — | **MCP tool calls** (e.g. place/cancel orders, create alerts) behind the confirmation gate | — |
| 21 | Outputs → Alerts | — | Agent can create broker alerts via tools | v2: app-level alert hub reading broker alerts |
| 22 | Service → APIs & Integrations | — | **MCP servers** (Robinhood first; any stdio/remote MCP) | More MCP servers = more integrations; v2 non-MCP APIs via a generic REST tool provider |
| 23 | Service → Multi-Channel Delivery | Streamlit UI | Streamlit UI | v2: CLI/notifications |
| 24 | Service → Automation & RPA | — | — | v2: scheduled agent runs (cron-like) with their own session |
| 25 | Governance → Safety & Alignment | Grounding-first system prompt | Confirmation gate; per-agent data scope; tool rounds cap; model-capability check | Policy engine v2 |
| 26 | Governance → Security & Privacy | `docs/security_design.md` | **Δ: new network egress** to remote MCP servers — explicit per-agent opt-in, documented §9 | — |
| 27 | Governance → Ethics & Fairness | — | Default templates carry "not financial advice; confirm every trade" language | — |
| 28 | Governance → Monitoring & Observability | — | `agent_activity_log` (per tool call) + Agents→Activity tab | Dashboards for it v2 |
| 29 | Governance → Evaluation & Feedback | — | — | v2: feedback loop |

---

## 5. Core design decisions

### D1 — Tool calling backend: Ollama-native function calling

**Original v1 decision:** agents use Ollama's `/api/chat` `tools` parameter and
`role: "tool"` result messages. No OpenAI-compatible side channel (oMLX etc.).

**Why:** verified working with local models; keeps the "all inference is
local" security story intact; `OllamaClient` already centralizes host/model/
`num_ctx`. **Alternative considered:** oMLX/OpenAI-compatible endpoint with
`openai` SDK — rejected for v1 (second provider surface, same local
inference, no capability gain; revisit if a model only exists there).

**Consequence:** an agent whose selected model lacks the `tools` capability
runs **without tools** and the UI says so (agent form warning + runtime
notice). Capability is read from `/api/tags` `details.capabilities`.

### D2 — MCP client: official `mcp` SDK (v2.3.0)

**Decision:** `mcp.client.streamable_http.streamable_http_client` for remote
servers, `mcp.client.stdio.stdio_client` for local ones,
`mcp.client.auth.OAuthClientProvider` for remote OAuth.

**Why:** Robinhood's endpoint is a standard OAuth 2.1 protected resource;
the SDK implements discovery (RFC 9728 + 8414), dynamic client registration
(RFC 7591), PKCE (S256), and refresh — the exact shape of the flow probed
from this machine. Hand-rolling OAuth against the vault is high-risk for
no gain.

**App-side duties (all v1 work):**

- `VaultTokenStorage` — `TokenStorage` implemented over vault keys
  `mcp_oauth_<server_id>` (tokens + registered client info, §6).
- `redirect_handler` — record the authorization URL, open the user's
  browser (`webbrowser.open`), show the URL in the UI.
- `callback_handler` — run a one-shot local HTTP listener on an ephemeral
  port (redirect URI `http://localhost:<port>/callback`), block up to
  `MCP_OAUTH_TIMEOUT_SECONDS` (default 300) for the `code`/`state`/`iss`
  query params, return `AuthorizationCodeResult`.

The whole flow runs inside a Streamlit button handler (`st.status` spinner);
it is a local-machine app, so blocking the rerun while the user clicks
"Authorize" in the browser is acceptable UX.

### D3 — Where agent state lives: the encrypted vault

**Decision:** every new record type is a vault key (encrypted at rest, same
DEK): `agents_index`, `mcp_servers`, `agent_activity_log`,
`agent_run_<session_id>` (transient, deleted on completion). Skills are
plain markdown files in `~/.personal_ai/skills/` — instructions are
non-sensitive and file-editable (documented §9, like `.chroma`).

**Why:** the security doc's cardinal rule — one DEK path, no new plaintext
locations for *personal* data. MCP OAuth **tokens are personal credentials**
→ vault, never a plaintext sidecar (contrast: opencode keeps its in
`~/.local/share/opencode/mcp-auth.json` mode 0600; we do better).

### D4 — The built-in "General" agent

**Decision:** a code-level constant agent (`id="general"`): today's exact
behavior (RAG on, `ChatEngine.SYSTEM_PROMPT`, no tools). Custom agents are
vault records. "General" is not editable/deletable.

**Why:** preserves existing behavior for users who never open the Agents
page; gives a stable default for new sessions.

### D5 — Confirmation gate for sensitive tools

**Decision:** per-agent `require_tool_confirmation` (default: on when the
agent has ≥1 MCP server). A tool call is **sensitive** if its name starts
with any of: `place_`, `create_`, `cancel_`, `update_`, `delete_`, `add_`,
`remove_`, `exercise_`, `follow_`, `unfollow_`, `mark_` (matches the
Robinhood MCP's read/write split: reads are `get_*`/`search`/`preview_*`).

When the loop hits a sensitive call:

1. The runtime **pauses** and persists the in-flight run state
   (`agent_run_<session_id>`: full message list + the pending call).
2. The UI renders the pending call (tool name + arguments JSON) with
   **Allow / Deny / Abort**.
3. Allow → the call executes and the loop resumes. Deny → a synthetic tool
   result `"DENIED by user. Do not retry this action; tell the user it was
   not performed and ask how to proceed."` is appended and the loop
   resumes (the model gets to react). Abort → the run state is dropped and a
   short "aborted" reply is recorded.

**Why:** this is a *trading* integration; the MCP tools' own descriptions
ask the agent to confirm, but prompt-level politeness is not enforcement.
A stateful gate is the only layer that actually holds. **Alternative
considered:** require the user to type "yes" — fragile (model-mediated,
paraphrasing), rejected.

### D6 — Reuse `ChatEngine` as the per-agent context builder

**Decision:** instead of duplicating the RAG/structured/budget machinery,
`ChatEngine` gains optional constructor parameters (defaults = today's
behavior, so all existing tests pass untouched):

- `model`, `context_window_tokens`, `temperature`, `max_tokens` — per-agent
  generation settings (passed to its `OllamaClient`),
- `system_prompt` — agent instructions (assembled: instructions + skills),
- `rag_scope_categories` — optional category list that **additionally
  filters** both the structured-record scan and the ChromaDB `where` clause.

`AgentRuntime` then: builds the prompt exactly like today
(`_build_prompt` → messages + sources), then runs the tool loop over those
messages. RAG scoping limits results to the agent's allowed categories.
When query analysis requests categories outside that scope, the current
implementation can fall back to retrieval across the allowed scope; it
does not guarantee an empty result or a particular model refusal.
Prompt wording is not the authorization boundary.

**Why:** one prompt-building code path (the tested one) instead of two;
per-agent budgets stay correct because budget math reads
`ollama_client.context_window_tokens`.

### D7 — Skills = local markdown files, injected into the system prompt

**Decision:** `~/.personal_ai/skills/<slug>.md` with a small frontmatter
(`name`, `description`) + body. An agent selects slugs; the runtime injects
each skill's description+body under `## Skill: <name>` headers into the
system prompt (counted against the context budget like the prompt itself).

**Why:** matches the ecosystem's skill concept (opencode/Claude skills),
stays file-editable, zero new dependencies (tiny built-in frontmatter
parse). Skills are *instructions*, not secrets → directory, not vault.

### D8 — Agent loop shape (runtime contract)

```
start_run(session_id, agent, query, history) -> RunResult
    messages = context_engine._build_prompt(...)      # system+history+user
    tools    = mcp_tools(agent)                        # [] if none/unsupported
    for round in 1..agent.max_tool_rounds:
        resp = ollama_chat(messages, tools=tools)      # streamed to UI
        messages += resp.message
        if resp.message.tool_calls:
            for call in tool_calls:
                if sensitive(call) and agent.confirm:  -> PAUSE (persist, await UI)
                result = mcp.call(server_of(call), call.name, call.args)
                log activity
                messages += {role:"tool", name, content: result_text}
        else:
            return COMPLETE(answer, sources, trail)
    return EXHAUSTED(rounds used; final answer from last round or notice)
resume_run(session_id, agent, decision) -> RunResult    # Allow/Deny/Abort
```

`RunResult` is a small dataclass: `status` ∈ {complete, exhausted,
awaiting_confirmation, aborted}, `answer`, `sources`, `trail`
(list of tool-call summaries for the UI), `pending` (the paused call).

**Why:** a pure function over (store, ollama, mcp) with an explicit pause
state — the only shape that survives Streamlit's rerun model *and* is
unit-testable with fakes. The UI is a thin projector over `RunResult`.

### D9 — Model capability and per-agent model choice

The agent form's model dropdown lists `OllamaClient.list_models()`; entries
whose `/api/tags` `capabilities` include `"tools"` are marked
`(tools)`. Selecting a non-tools model for an agent with MCP servers shows
a warning (tools will be inactive at runtime). Per-agent
`context_window_tokens` defaults to 8192 with a "use model's window" helper
that reads `details.context_length`.

### D10 — Session ↔ agent binding

A session record gains optional `agent_id`. Selecting an agent in the chat
selector updates the binding on the next sent message (`touch_session`).
History is shared across agents within a session (one conversation,
multiple personas — each turn's system prompt comes from that turn's agent).
"New Chat" starts unbound (General).

---

## 6. Data model

All JSON in the vault (encrypted). `*_at` are ISO-8601.

### `agents_index` → `{"agents": [Agent]}`

```jsonc
{
  "id": "uuid-hex",
  "name": "Robinhood Trader",          // display name, unique-ish (UI-enforced)
  "description": "My portfolio copilot",
  "icon": ":material/candlestick_chart:",  // Material Symbols name
  "model": "qwen3.8:27b-mlx",
  "temperature": 0.3,
  "max_tokens": 2048,
  "context_window_tokens": 16384,
  "instructions": "You manage the user's own Robinhood portfolio...", // system prompt core
  "rag_enabled": true,
  "rag_scope_categories": ["brokerage"],   // [] = unrestricted
  "mcp_server_ids": ["uuid-hex"],
  "skills": ["trading-discipline"],        // slugs
  "max_tool_rounds": 6,
  "require_tool_confirmation": true
}
```

### `mcp_servers` → `{"servers": [McpServer]}`

```jsonc
{
  "id": "uuid-hex",
  "name": "Robinhood Trading",
  "kind": "remote",                  // "remote" | "stdio"
  "url": "https://agent.robinhood.com/mcp/trading",   // remote
  "command": "", "args": [],          // stdio
  "enabled": true,
  "last_status": "connected",        // connected | needs_auth | error | disabled
  "last_error": null,
  "tool_count": 41,
  "updated_at": "..."
}
```

### `mcp_oauth_<server_id>` (created by `VaultTokenStorage`)

```jsonc
{
  "tokens":      { "access_token": "...", "token_type": "Bearer",
                   "expires_in": 3600, "scope": "internal",
                   "refresh_token": "..." },
  "client_info": { "client_id": "...", "redirect_uris": ["http://localhost:.../callback"], ... },
  "updated_at": "..."
}
```

### `agent_activity_log` → `{"entries": [...]}` (capped, oldest dropped)

```jsonc
{
  "ts": "...", "session_id": "...", "agent_id": "...", "agent_name": "...",
  "server": "Robinhood Trading", "tool": "get_equity_quotes",
  "args_digest": "symbols=[AAPL]",        // truncated, one line
  "ok": true, "duration_ms": 812, "error": null
}
```

### `agent_run_<session_id>` (transient; deleted on completion/abort)

```jsonc
{
  "agent_id": "...",
  "messages": [ /* full Ollama message list incl. pending assistant tool_calls */ ],
  "pending": { "server": "...", "tool": "place_equity_order", "args": { ... } },
  "sources": [...],
  "trail": [ { "tool": "get_equity_quotes", "summary": "ok (812ms)" } ],
  "created_at": "..."
}
```

### Skills: `~/.personal_ai/skills/<slug>.md`

```markdown
---
name: Trading Discipline
description: Force confirmation and conservative framing for trade actions
---
Before recommending any trade, state position size, estimated cost, and risk...
```

### Session record addition

`chat_sessions_index` sessions gain optional `"agent_id"`.

---

## 7. Agent runtime sequence (with confirmation)

```
User                UI (chat)            AgentRuntime            McpManager        Model/Ollama
 │  msg ───────────▶ │                        │                      │                 │
 │                   │ start_run() ──────────▶│                      │                 │
 │                   │                        │ build prompt (RAG) ───────────────────▶│
 │                   │                        │ chat(messages,tools)──────────────────▶│
 │  ◀─ streamed ─────│◀── text deltas ─────────────────────────────────────────────────│
 │                   │                        │  ◀─ tool_calls:[get_equity_quotes] ────│
 │                   │                        │ call_tool() ────────▶│ (MCP roundtrip) │
 │                   │                        │  ◀── result ─────────│                 │
 │                   │                        │ chat(messages+tool) ──────────────────▶│
 │  ◀─ streamed ─────│◀── final answer ────────────────────────────────────────────────│
 │                   │                        │ COMPLETE: save exchange, log, cleanup  │
```

Sensitive-tool variant: at `call_tool()` the runtime instead returns
`awaiting_confirmation` + persists `agent_run_<session>`; the UI renders the
gate; on **Allow** the user's rerun calls `resume_run(decision=allow)` which
reloads the run state, executes, and continues exactly where it paused.

## 8. MCP manager contract (`src/agents/mcp.py`)

```python
class McpManager:
    def __init__(self, vault): ...
    def connect(self, server_id, *, interactive=False): ...
    def list_tools(self, server_id): ...
    def call_tool(self, server_id, name, args): ...
```

- `McpTool` includes `{server_id, name, description, input_schema, is_write}`.
  (`is_write` from MCP `annotations.readOnlyHint == False` when
  present, else the prefix heuristic — MCP annotations are the *primary*
  signal when a server provides them; the prefix list is the fallback for
  servers without annotations).
- Public operations are synchronous wrappers around fresh MCP sessions,
  not a process-wide connection cache. `connect(..., interactive=True)`
  enables the user-initiated OAuth flow. A failed connection reports status
  and the agent runs
  without that server's tools (UI shows the error; the agent is never
  silently broken).
- Tool results: `CallToolResult.content` text parts are joined;
  `structured_content` (JSON) is preferred when present; `is_error=True`
  results are fed back to the model as text (it can react) — never raised
  into the UI mid-loop.
- Result text is truncated to ~8k chars before entering model context
  (configurable), with a `[truncated]` marker.

## 9. Security & governance delta

New in v1, recorded per the repo's security checklist:

1. **Network egress.** Remote MCP servers receive tool names and arguments,
   not the complete model message list. Arguments may reproduce private
   user messages, history or retrieved context; tool results return to the
   model. OAuth credentials transit to configured authorization services.
   This is an
   **explicit, per-agent opt-in** (agent only gets a server's tools if the
   user attached that server). Mitigations: localhost-only app unchanged;
   per-agent RAG scoping limits what context an agent can pull;
   `rag_enabled: false` removes retrieved document context, not private user
   text or conversation history.
   Default template text warns the user. This is the biggest threat-model
   delta and is called out as accepted risk (like `.chroma` plaintext).
2. **Token storage.** OAuth tokens + registered-client info live in the
   encrypted vault (`mcp_oauth_<id>`), not a plaintext file. The `mcp` SDK
   refreshes transparently; a refresh failure returns the agent to
   `needs_auth` (UI shows "Reconnect").
3. **Action safety.** When enabled, the confirmation gate (D5) is enforced
   by the runtime, not the prompt. The encrypted activity log records
   executed/denied activity; unknown tool requests are not guaranteed audit
   entries. This is not complete provider/model/tool-attempt coverage.
4. **No new key paths.** Agent/MCP code receives the existing
   AuthManager-provided vault key; it does not derive keys independently or
   read a legacy raw-key file.
5. **Skill files** at `~/.personal_ai/skills/` are plaintext *instructions*.
   Their arbitrary user-written contents are not enforced to be non-sensitive;
   keep personal data and credentials out of them.
6. **Stdio MCP servers** run as local child processes; the add form is
   user-explicit (they type the command). Not enabled by default for
   anyone; same trust level as any CLI the user runs.

## 10. v2 roadmap (interfaces already in place)

- **Planner/orchestrator agent:** the tool loop accepts tools from any
  provider — add an *internal* tool provider exposing
  `dispatch_agent(agent_id, task)`, which runs a sub-agent's
  `start_run` in its own transient session and returns the answer. Task
  decomposition needs typed task state, inherited permissions and shared
  budgets; an internal dispatch tool alone does not establish safe coordination.
- **Long-term memory:** periodic extraction of durable facts from
  sessions into vault records, injected per agent like skills.
- **Feedback/evaluation:** 👍/👎 + note per answer → vault; surface in
  Agents→Activity; later, a "reflection" agent summarizes recurring issues.
- **Scheduled agents (deferred):** a separately designed lifecycle-managed
  local scheduler could start
  `start_run` for configured agents on a cadence (e.g. morning portfolio
  brief) into dedicated sessions.
- **Outputs:** charting tool (plotly → static image) for "Outputs →
  Visualizations"; broker alerts surfaced in an Alerts hub.
- **Inputs:** image messages (vision models already local).

## 11. Testing strategy

| Module | Tests | Mode |
|---|---|---|
| `src/agents/store.py` | agent/MCP CRUD, activity log cap, run-state save/load/delete | real temp `DataVault` (repo pattern) |
| `src/agents/mcp.py` | `to_ollama_tool` mapping, sensitive-tool detection (prefix + annotations), result text extraction (fake `CallToolResult`) | pure, no network |
| `src/agents/runtime.py` | full loop: scripted fake Ollama (tool_call → final), fake MCP backend; confirmation pause/resume allow+deny+abort; round cap; non-tools model fallback | pure fakes (pattern of `tests/test_chat_engine.py`) |
| `ChatEngine` params | default behavior unchanged; scope filter restricts records+RAG | existing + new cases |
| UI | `AppTest`: main.py renders Agents page (fake vault w/ seeded agent + MCP server), agent selector present on Chat, no exceptions | `streamlit.testing.v1` per AGENTS.md |

## 12. File layout (v1)

```
src/agents/
  __init__.py
  store.py      # vault-backed CRUD: agents, mcp servers, activity, run state
  skills.py     # skills directory: list/create/update/delete, frontmatter parse
  mcp.py        # McpManager: connections, OAuth (vault token storage), tool mapping
  runtime.py    # AgentRuntime: prompt build (via ChatEngine), tool loop, gate
src/interface/
  agents.py     # Agents page (tabs: Agents / MCP Servers / Skills / Activity)
src/ai_engine/
  ollama_client.py   # + tools param on chat(); model capability helper
  chat_engine.py     # + optional ctor params (model, system prompt, rag scope)
docs/multi_agent_design.md   # this file
tests/test_agent_store.py, test_mcp_tools.py, test_agent_runtime.py
```
