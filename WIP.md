# WIP — Multi-Agent System + Robinhood MCP Integration

Handoff/reference doc for the multi-agent layer. **Read `AGENTS.md` and
`docs/multi_agent_design.md` first** — the design doc is the source of
truth for architecture and decisions D1–D10; `docs/security_design.md`
("Multi-agent and MCP integration" section) records the security deltas.
This file keeps only what isn't in the code or those docs: live-verified
external API details (don't re-probe), implementation gotchas discovered
the hard way, and the current state.

## Current state (2026-10-18)

**Build complete and tested.** Full multi-agent layer (agents, MCP servers,
skills, confirmation gate, activity log, Agents page, chat agent selector)
is in the workspace. `make test` → **297 passed**; `make check-context`
✅. The default/General-agent path is byte-identical to before the
multi-agent work (all pre-existing tests pass untouched).

**Enhancement: oMLX engine support added.** The system now supports both
Ollama and oMLX inference engines, selectable at startup (via environment
variables) and in the UI Settings page. `make test` still passes with
all 297 tests. 

The oMLX support includes:
- oMLX as a second LLM inference engine
- oMLX embeddings support with Qwen3-VL-Embedding-2B-8bit model
- Markdown-aware chunking for better RAG performance
- Collection bump from v2 to v3 to accommodate embedding model change
- API key authentication support for oMLX servers
- Proper model existence checking for oMLX models
- Engine-aware error messages to distinguish between Ollama and oMLX issues

The manual end-to-end Robinhood check remains for testing MCP integration:
Everything up to the browser OAuth consent click is built and testable
headlessly; the consent itself requires the human user:

1. `make run` → **Agents** → MCP Servers → add remote server, url
   `https://agent.robinhood.com/mcp/trading` → **Connect & Authorize**
   (browser opens; sign in; the button waits up to
   `MCP_OAUTH_TIMEOUT_SECONDS`). Status should flip to Connected with a
   tool count.
2. New agent "Trading" (model `qwen3.8:27b-mlx`, that server attached,
   confirmation on). The RAG-egress warning is expected while RAG is on.
3. **Chat** → pick "Trading" → "what's the price of AAPL?" (read tool —
   must NOT hit the gate), then "buy 1 share of AAPL" (the confirmation
   card MUST appear).
4. Verify: **Activity** tab shows both calls with args/outcome/duration;
   **Files** page shows no `mcp_oauth_` / `agent_run_` entries.

If tool-calling is flaky on one model, the other verified tool-capable
models are `gemma4:31b-mlx`, `gpt-oss:20b`, `ornith-1.5:35b`.

## What exists where

- `src/agents/store.py` — vault CRUD. Keys: `agents_index`, `mcp_servers`,
  `agent_activity_log` (capped 500), `agent_run_<session_id>` (transient
  paused-run state), plus `mcp_oauth_<server_id>` (written by
  `mcp.py`'s `VaultTokenStorage`). General agent is a code constant
  (`general_agent()`), not a vault record. Degrades on `DataVaultError`
  like `chat_sessions.py`.
- `src/agents/skills.py` — markdown skills in `~/.personal_ai/skills/`
  (override via `PERSONAL_AI_SKILLS_DIR`); hand-parsed two-field
  frontmatter (no yaml dep); `render_skills_for_prompt(slugs)`.
- `src/agents/mcp.py` — `McpManager` (fresh transport+session per
  operation via `asyncio.run`; no connection caching — see gotchas),
  `VaultTokenStorage` (the SDK's TokenStorage protocol, vault-backed),
  `_OAuthCallbackServer` (ephemeral 127.0.0.1 listener), `McpTool`
  (`is_write` from MCP `readOnlyHint`, prefix fallback),
  `ToolResult`, `extract_tool_result_text`, `to_ollama_tool`,
  `is_sensitive_tool`.
- `src/agents/runtime.py` — `AgentRuntime.start_run/resume_run/active_run`
  over `RunResult(status, answer, sources, trail, pending)`; statuses
  `complete | exhausted | awaiting_confirmation | aborted`. The
  confirmation gate lives here (not in the prompt). Tool errors are fed
  back to the model, never raised. `make_context_engine=` is the injection
  point tests use; the default builds a per-agent `ChatEngine`.
- `src/ai_engine/chat_engine.py` — optional per-agent ctor params
  (`model`, `context_window_tokens`, `system_prompt`,
  `rag_scope_categories`); omitted = historical behavior.
- `src/ai_engine/ollama_client.py` — `chat(..., tools=None)` (passed only
  when non-empty), `model_details()` → `model_capabilities()` →
  `model_supports_tools()`.
- `src/config.py` — `SKILLS_DIR`, `DEFAULT_AGENT_MAX_TOOL_ROUNDS`,
  `MCP_OAUTH_TIMEOUT_SECONDS`, `MCP_TOOL_RESULT_MAX_CHARS`.
- `src/interface/agents.py` — Agents page (tabs: Agents / MCP Servers /
  Skills / Activity). Session-cached `McpManager` keyed on vault identity.
- `src/interface/main.py` — "Agents" nav page; agent selectbox in the
  chat session-controls row (key `active_agent_id`; the session record's
  `agent_id` is the default, per D10); send path routes General →
  `_respond` (unchanged streaming path), other agents →
  `AgentRuntime` via `_respond_with_agent`; paused runs render the
  Allow/Deny/Abort card and disable the input; session-cached
  `AgentRuntime` (key `agent_runtime`).
- `src/data_vault/vault.py` — internal keys incl. `agents_index`,
  `mcp_servers`, `agent_activity_log` and prefixes `agent_run_`,
  `mcp_oauth_` (keeps tokens/run state out of Files, sidebar count, RAG).
- Tests (all offline): `test_agent_store.py` (22), `test_mcp_tools.py`
  (39), `test_agent_runtime.py` (33), `test_chat_engine_agent_params.py`
  (16), `test_agents_page_smoke.py` (6, AppTest with throwaway HOME —
  the pattern for any future UI test).

## Implementation gotchas (discovered the hard way)

- **Never cache MCP sessions/connections across operations.** Connections
  bind to the asyncio loop that created them; the app runs
  `asyncio.run(...)` per operation (Streamlit scripts have no running
  loop). Every `list_tools`/`call_tool`/`connect` opens a fresh transport
  + session (one extra initialize round-trip — accepted).
- **Paused-run state lives in the vault** (`agent_run_<session_id>`),
  never only in `st.session_state`: Streamlit reruns the whole script per
  interaction, and session_state is per-browser-tab and dies with the
  server. A half-finished trade must survive reruns and restarts.
- **Keep `ChatEngine`'s default behavior byte-identical.** The pre-existing
  test suite and the General agent depend on it; the per-agent ctor params
  must all be optional no-ops.
- `mcp` SDK 2.x specifics: `types.ToolAnnotations`' field is
  **`read_only_hint`** (snake_case), and `Tool.annotations` may be None.
  For remote transports use the SDK's `create_mcp_http_client(auth=provider)`
  — when you create the client yourself the SDK does not close it
  (`mcp.py` closes it in `finally`). `VaultTokenStorage` must
  `model_dump(mode="json")` — plain `model_dump()` leaves `AnyUrl` objects
  the vault's JSON serializer rejects.
- Non-interactive OAuth provider: the SDK validates that **both**
  `redirect_handler` and `callback_handler` are present before use, even
  though neither is called; both raise the `_NeedsAuth` marker, classified
  as `McpError(needs_auth=True)` everywhere. Interactive: `webbrowser.open`
  + callback wait run via `asyncio.to_thread` (never block the loop).
- `ClientSession(read, write, read_timeout_seconds=60.0)` bounds a hung
  server (`_MCP_SESSION_READ_TIMEOUT_SECONDS` in `mcp.py`).
- Tool-name collisions across an agent's servers: first server wins —
  consistently for the exposed schema, the `is_write` gate, and call
  routing (a regression here was caught by
  `tests/test_agent_runtime.py::test_tool_name_collision_first_server_wins`).
- The exhausted-run answer is the model's last non-empty **assistant**
  content only — never a tool result or the user's own query.
- The human user must complete the Robinhood OAuth consent in the browser
  — an agent cannot click through it.
- **oMLX Integration fixes**: When running with oMLX engine, the system now
  properly handles model existence checking and error messages without 
  triggering Ollama-related errors.

## Environment facts (verified 2026-10-18)

- Python 3.12.14, project venv at `./venv` (all dev via `venv/bin/...`).
- `mcp` 2.3.0 in the venv (pulled `httpx2 2.13.1`, `mcp-types`, `pyjwt`,
  `sse-starlette`, `truststore`).
- Ollama at `localhost:11434`. Tool-capable models: `qwen3.8:27b-mlx`,
  `gemma4:31b-mlx`, `gpt-oss:20b`, `ornith-1.5:35b` (`/api/tags`
  `details.capabilities` includes `"tools"`).
- oMLX OpenAI-compatible server at `127.0.0.1:8000` — now supported as
  an alternative inference engine with API key authentication.
- Makefile: `make test` = `PYTHONPATH=. pytest tests/ -v`;
  `make check-context`; `make run` = Streamlit. No CI.

## Verified: Robinhood MCP OAuth (do not re-probe)

Remote streamable-HTTP MCP, **OAuth 2.1 authorization-code + PKCE, public
client, dynamic registration, no device-code grant → a browser flow is
mandatory**:

- Endpoint: `https://agent.robinhood.com/mcp/trading` (POST-only; GET → 405;
  unauthenticated POST → 401 with
  `www-authenticate: Bearer
  resource_metadata="https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"`).
- AS metadata (at the resource URL): `authorization_endpoint:
  https://robinhood.com/oauth`, `token_endpoint:
  https://api.robinhood.com/oauth2/token/`, `registration_endpoint:
  https://agent.robinhood.com/oauth/trading/register`, `grant_types:
  [authorization_code, refresh_token]`, PKCE `S256` only,
  `token_endpoint_auth_methods_supported: ["none"]`, `scopes_supported:
  ["internal"]`, `authorization_response_iss_parameter_supported: true`.
- opencode caches its own tokens in
  `~/.local/share/opencode/mcp-auth.json`. **Do not copy/reuse them** — by
  design this app runs its own client registration and stores tokens in
  the encrypted vault (`mcp_oauth_<server_id>` keys).

## Verified: Ollama tool calling (do not re-probe)

- `/api/chat` accepts `tools: [OpenAI-style schemas]`; response
  `message.tool_calls: [{"id", "function": {"name", "arguments": {...}}}]`.
- Round-trip works: append the assistant `tool_calls` message +
  `{"role": "tool", "name": <tool>, "content": "<json-string>"}` → model
  produces the final text answer.
- Streaming: `tool_calls` arrive in a pre-`done` chunk. (v1 runs rounds
  non-streaming anyway — streaming is a v1.1 polish, plumbing exists.)
- `ollama` python client 0.6.2 `Client.chat(...)` accepts `tools`
  (verified via `inspect.signature`).

## Verified: oMLX API Key Authentication (do not re-probe)

- oMLX servers require API key authentication via Bearer token
- Authentication header: `Authorization: Bearer <api_key>`
- API key loaded from environment variables `omlx_api_key` or `OMLX_API_KEY`
- `.env` file support implemented for development environment

## Verified: `mcp` SDK 2.3.0 API (do not re-probe — 2.x differs from 1.x docs)

```python
# Remote transport (NOTE: underscore name; 1.x was streamablehttp_client)
from mcp.client.streamable_http import streamable_http_client
#   streamable_http_client(url, *, http_client: httpx2.AsyncClient | None = None,
#                          terminate_on_close=True, max_sse_event_size=...)
#   -> AsyncGenerator[(read_stream, write_stream)]

# Local stdio transport
from mcp.client.stdio import stdio_client, StdioServerParameters
#   stdio_client(StdioServerParameters(command, args, env, cwd, ...))
#   -> AsyncGenerator[(read_stream, write_stream)]

# Session (async context manager; __aenter__ runs initialize)
from mcp.client.session import ClientSession
#   async with ClientSession(read, write) as session:
#       await session.list_tools()          -> types.ListToolsResult (.tools)
#       await session.call_tool(name, args) -> types.CallToolResult

# OAuth (mcp 2.x): provider is an httpx2.Auth — attach via httpx2.AsyncClient(auth=provider)
from mcp.client.auth import OAuthClientProvider, TokenStorage
#   OAuthClientProvider(server_url, client_metadata, storage,
#                       redirect_handler=None, callback_handler=None)
#   client_metadata: mcp.shared.auth.OAuthClientMetadata (pydantic):
#     redirect_uris REQUIRED; defaults: response_types=['code'],
#     grant_types=['authorization_code','refresh_token'],
#     application_type='native', token_endpoint_auth_method=None, scope=None
#   redirect_handler: async (authorization_url: str) -> None   # we open the browser
#   callback_handler: async () -> AuthorizationCodeResult(code, state, iss)
#   The SDK itself does: PRM + AS metadata discovery, dynamic client
#   registration, PKCE, token refresh. Triggered lazily on first 401.

from mcp.shared.auth import OAuthToken, OAuthClientInformationFull, AuthorizationCodeResult
#   OAuthToken(access_token, token_type, expires_in, scope, refresh_token)

from mcp import types
#   types.Tool: name, title, description, input_schema, annotations, ...
#     annotations.read_only_hint (bool|None) is the authoritative read/write signal
#   types.CallToolResult: content (list[TextContent|...]), structured_content, is_error
#   types.TextContent(type='text', text=...)
```

**`TokenStorage` is a Protocol with four ASYNC methods** (in
`mcp.client.auth`): `get_tokens() -> OAuthToken | None`,
`set_tokens(OAuthToken)`, `get_client_info() -> OAuthClientInformationFull |
None`, `set_client_info(OAuthClientInformationFull)`.

## Future work (v2 candidates)

- **Streaming tool rounds.** v1 runs every round non-streaming (the UI
  shows one `st.status` for the whole run); the chat plumbing for streams
  exists — per-round streaming would make long tool chains feel alive.
- **More MCP servers** — the layer is server-agnostic (remote + stdio);
  e.g. local filesystem/calendar servers are one Agents-page entry away.
- The rest of the roadmap lives in `docs/multi_agent_design.md` §10
  (agent orchestration, per-agent memory, tool-result budgets, ...).
