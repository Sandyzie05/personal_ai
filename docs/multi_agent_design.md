# Multi-agent runtime and MCP design (v1 reference)

How user-defined agents, the tool loop, MCP servers, skills and the confirmation gate work (`src/agents/`).
This is the **runtime reference**. The coordinator layer built on top of it (planner, parallel specialists, scoped bus,
quarantine, Core Memory gate, scheduler, policy) is documented in [platform_plan.md](platform_plan.md); security in
[security_design.md](security_design.md).

**Superseded since v1:** D1 below (Ollama-only) - the default engine is now oMLX with an OpenAI-shape tool adapter
([ai_engine.md](ai_engine.md)); and the v1 non-goals "no planner / no sub-agent dispatch / no scheduled agents" (all built in
`src/agents/orchestration/`). Section numbers are kept stable because source comments reference them.

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
4. **Safety for actions.** Tools that mutate state (placing trades, creating
   alerts) require an explicit in-UI confirmation from the user before
   execution.
5. **Keep the security posture.** All new state (agent configs, MCP
   configs, OAuth tokens, activity log, run state) lives in the encrypted
   vault. No new plaintext-at-rest location except skill instruction files
   (non-sensitive, documented in §9).
6. **Testable without a browser.** Core logic (store, tool mapping, agent
   loop) is pure-Python and unit-tested with fakes; UI is smoke-tested with
   `streamlit.testing.v1.AppTest`.

### Non-goals (v1, all since addressed except the last)

Sub-agent dispatch, multi-provider backends, scheduled agents and long-term memory were deferred in v1; the coordinator layer now covers dispatch,
scheduling and approval-gated memory. Still out of scope: image/audio/video input, knowledge graph.

---

## 2. Building blocks the agent layer reuses

| Piece | File | Role |
|---|---|---|
| Login gate + key lifecycle | `src/security/auth.py` | Password to wrapped DEK; DEK only in `st.session_state` |
| Encrypted vault | `src/data_vault/` | AES-256-GCM values in SQLite |
| RAG + chat engine | `src/ai_engine/` | Per-agent context builder (D6) |
| Sessions | `src/interface/chat_sessions.py` | Vault key `chat_sessions_index` |
| MCP | `mcp` Python SDK | Streamable HTTP + stdio, OAuth 2.1 + PKCE with vault token storage (D2) |

## 3-4. Architecture and reference-diagram mapping

Removed here (they described the v1 plan and are now out of date). Current architecture: [architecture.md](architecture.md).
Open items from that mapping (vision inputs, knowledge graph, voice) are tracked in [platform_plan.md](platform_plan.md) §6.1.

## 5. Core design decisions

### D1 — Tool calling backend (superseded: now oMLX with an OpenAI-shape adapter; text below is the original Ollama decision)

**Decision:** agents use Ollama's `/api/chat` `tools` parameter and
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
messages. RAG scoping composes with the existing query-analysis scoping
(union of both: if the query names a category outside the agent's scope,
retrieval is empty for that query — the agent honestly says "outside my
scope" because the grounding prompt forces it to).

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
    def connect(self, server: McpServer) -> Connection   # lazy per-server session
    async def list_tools(self, server_id) -> list[McpTool]
    async def call_tool(self, server_id, name, args) -> ToolResult
    def to_ollama_tool(self, tool: McpTool) -> dict      # {"type":"function","function":{...}}
    def connect_remote_with_oauth(self, server_id)       # starts browser flow (UI button)
    @staticmethod
    def is_sensitive_tool(name: str) -> bool
```

- `McpTool`: `{server_id, name, description, input_schema, annotated_write: bool}`
  (`annotated_write` from MCP `annotations.readOnlyHint == False` when
  present, else the prefix heuristic — MCP annotations are the *primary*
  signal when a server provides them; the prefix list is the fallback for
  servers without annotations).
- Connections are cached per server id for the Streamlit process; a failed
  connect degrades to `last_status="error"` and the agent simply runs
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

1. **Network egress.** Remote MCP servers (Robinhood) receive the user's
   messages, retrieved vault context (if RAG is on for that agent), and
   tool results; OAuth tokens transit to `api.robinhood.com`. This is an
   **explicit, per-agent opt-in** (agent only gets a server's tools if the
   user attached that server). Mitigations: localhost-only app unchanged;
   per-agent RAG scoping limits what context an agent can pull;
   `rag_enabled: false` removes vault context from tool calls entirely.
   Default template text warns the user. This is the biggest threat-model
   delta and is called out as accepted risk (like `.chroma` plaintext).
2. **Token storage.** OAuth tokens + registered-client info live in the
   encrypted vault (`mcp_oauth_<id>`), not a plaintext file. The `mcp` SDK
   refreshes transparently; a refresh failure returns the agent to
   `needs_auth` (UI shows "Reconnect").
3. **Action safety.** Confirmation gate (D5) is enforced by the runtime,
   not the prompt. Audit: every tool call (args digest, outcome, duration)
   in `agent_activity_log`.
4. **No new key paths.** Vault DEK flow is untouched; no code reads
   `~/.personal_ai_auth` or the old key file.
5. **Skill files** at `~/.personal_ai/skills/` are plaintext *instructions*
   (no personal data) — accepted, mirroring the `.chroma` tradeoff.
6. **Stdio MCP servers** run as local child processes; the add form is
   user-explicit (they type the command). Not enabled by default for
   anyone; same trust level as any CLI the user runs.

## 10. v2 status

Built: coordinator + parallel specialists (instead of a `dispatch_agent` tool), scheduled runs, approval-gated Core Memory (instead of automatic
fact extraction), request policy, quarantine and scoped bus. Still open: per-answer feedback loop, charting tool, image input, scheduler UI
([platform_plan.md](platform_plan.md) §6.1).

## 11. Testing strategy

| Module | Tests | Mode |
|---|---|---|
| `src/agents/store.py` | agent/MCP CRUD, activity log cap, run-state save/load/delete | real temp `DataVault` (repo pattern) |
| `src/agents/mcp.py` | `to_ollama_tool` mapping, sensitive-tool detection (prefix + annotations), result text extraction (fake `CallToolResult`) | pure, no network |
| `src/agents/runtime.py` | full loop: scripted fake Ollama (tool_call → final), fake MCP backend; confirmation pause/resume allow+deny+abort; round cap; non-tools model fallback | pure fakes (pattern of `tests/test_chat_engine.py`) |
| `ChatEngine` params | default behavior unchanged; scope filter restricts records+RAG | existing + new cases |
| UI | `AppTest`: main.py renders Agents page (fake vault w/ seeded agent + MCP server), agent selector present on Chat, no exceptions | `streamlit.testing.v1` per AGENTS.md |

## 12. File layout

```
src/agents/            store.py (vault CRUD) · skills.py · mcp.py (McpManager) · runtime.py (AgentRuntime, tool loop, gate)
src/agents/orchestration/   coordinator, bus, quarantine, memory, policy, workers, ingest, schedule, templates, service, skill_pack
src/interface/agents.py     Agents page (Agents / MCP Servers / Skills / Activity / Core Memory)
skills/                built-in skill files, installed to ~/.personal_ai/skills
tests/                 test_agent_store.py, test_mcp_tools.py, test_agent_runtime.py, test_orchestration*.py
```

## Coordinator additions (platform_plan B3-B9, B15)

- **Specialists** `builtin-expense|forecaster|market` are selectable in Chat; `service.run_query(only=role)` keeps just that role's steps.
- **Optional LLM planner** (`PERSONAL_AI_LLM_PLANNER=true`, default off): `workers.llm_plan` proposes a `Plan`; the `Plan` schema and `validate_plan` gate it, any failure falls back to `plan_for`.
- **Gmail/brokerage** workers (`email`, `brokerage`) exist only when an enabled MCP server named like Gmail / a broker is bound. MCP reads are prefetched on the calling thread (vault/OAuth, S15); workers get the result only. Holdings keep only validated tickers and reach `market` via the `coordinator→market` `HoldingsSummary` edge.
- **Verifier** (`verifier.verify_claims`): figures must occur in cited record evidence; a model may only add an `unsupported` escalation for non-numeric claims.
- **Cancellation:** timed-out steps get a cancel event; workers call `coordinator.check_cancel()` between provider calls. `MessageBus.send` dedupes identical re-sends (retries).
- **Scheduler:** Agents → Scheduled tab; due jobs run once per session at app open (`service.run_scheduled`) or from cron; `flock` makes claim-and-run atomic across processes. Results are proposals only.
- **Expense worker (B15):** category filter from the question, superseded records skipped, each (provider, period) counted once, delta `uncertain` on conflicts / partial quarters / mixed categories.
