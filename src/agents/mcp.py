"""MCP client layer: Model Context Protocol servers as agent tools.

Talks to any MCP server - remote streamable-HTTP (with OAuth 2.1
authorization-code + PKCE via the `mcp` SDK) or local stdio - and exposes
its tools in the shape Ollama's native function calling expects.

Design decisions (docs/multi_agent_design.md D2/D5):

- No connection caching. Streamlit scripts have no running asyncio loop, so
  every operation runs inside its own `asyncio.run` - and the SDK's
  transport streams bind to the loop that created them, so a session cached
  from a previous operation would be dead. Every list_tools/call_tool opens
  a fresh transport + session (one extra initialize round-trip, accepted).
- OAuth tokens and the dynamically-registered client info are encrypted
  vault records (`mcp_oauth_<server_id>` via VaultTokenStorage) - never a
  plaintext sidecar. The SDK refreshes transparently; when re-authorization
  is needed the operation surfaces `needs_auth` instead of hanging.
- `connect(..., interactive=True)` is the only code path that may open a
  browser: an ephemeral 127.0.0.1 listener catches the OAuth redirect and
  blocks (in a worker thread) up to MCP_OAUTH_TIMEOUT_SECONDS.
"""

import asyncio
import json
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from mcp import types
from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from src.agents import store
from src.config import MCP_OAUTH_TIMEOUT_SECONDS, MCP_TOOL_RESULT_MAX_CHARS
from src.data_vault import DataVaultError

# A hung MCP server (remote or stdio) must not wedge the Streamlit rerun
# thread forever; this bounds any single read from the server.
_MCP_SESSION_READ_TIMEOUT_SECONDS = 60.0

# Tool-name prefixes that mark a call as state-mutating when the server
# provides no MCP annotations (design D5: matches the Robinhood MCP's
# read/write split - reads are get_*/search/preview_*).
SENSITIVE_TOOL_PREFIXES = (
    "place_",
    "create_",
    "cancel_",
    "update_",
    "delete_",
    "add_",
    "remove_",
    "exercise_",
    "follow_",
    "unfollow_",
    "mark_",
)


class McpError(Exception):
    """MCP operation failure (unknown server, transport, auth, or tool).

    `needs_auth` distinguishes "the server requires (re-)authorization" so
    the UI can offer Connect & Authorize instead of showing a raw error.
    """

    def __init__(self, message: str, needs_auth: bool = False):
        super().__init__(message)
        self.needs_auth = needs_auth


class _NeedsAuth(McpError):
    """Internal marker raised by the non-interactive auth stubs."""

    def __init__(self):
        super().__init__("authorization required", needs_auth=True)


@dataclass
class McpTool:
    """One tool from one server, in app-internal form."""

    server_id: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    is_write: bool


@dataclass
class ToolResult:
    """A tool call's outcome, always fed back to the model as text.

    `ok=False` results (is_error from the server) are NOT raised - the model
    sees the error text and can react (retry differently, ask the user).
    """

    ok: bool
    text: str


def _to_mcp_error(exc: Exception, server_name: str) -> McpError:
    if isinstance(exc, _NeedsAuth):
        return McpError(
            f"Server '{server_name}' requires authorization - use "
            "Connect & Authorize on the Agents page",
            needs_auth=True,
        )
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, OAuthFlowError):
        # Covers failed refresh, unusable registration, token exchange -
        # the remediation for all of them is (re-)authorization.
        return McpError(
            f"Authorization problem with server '{server_name}': {exc}",
            needs_auth=True,
        )
    return McpError(f"MCP error: {type(exc).__name__}: {exc}")


class VaultTokenStorage:
    """The mcp SDK's TokenStorage protocol backed by the encrypted vault.

    One vault record per server, key `mcp_oauth_<server_id>`::

        {"tokens": {...OAuthToken fields...},
         "client_info": {...registered client...},
         "updated_at": "..."}

    Reads degrade to None on DataVaultError (a decrypt failure under a
    changed vault key means "no stored credentials", which sends the SDK
    down the re-authentication path instead of crashing the operation).
    """

    def __init__(self, vault: Any, server_id: str):
        self.vault = vault
        self.server_id = server_id
        self.key = f"mcp_oauth_{server_id}"

    def _load(self) -> Dict[str, Any]:
        try:
            data = self.vault.retrieve_data(self.key)
        except DataVaultError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, record: Dict[str, Any]) -> None:
        record["updated_at"] = datetime.now().isoformat()
        self.vault.store_data(self.key, record)

    def _token_from(self, raw: Any) -> Optional[OAuthToken]:
        if not isinstance(raw, dict) or not raw.get("access_token"):
            return None
        fields = {k: v for k, v in raw.items() if k in OAuthToken.model_fields}
        try:
            return OAuthToken(**fields)
        except Exception:
            return None

    def _client_info_from(self, raw: Any) -> Optional[OAuthClientInformationFull]:
        if not isinstance(raw, dict) or not raw.get("client_id"):
            return None
        fields = {
            k: v
            for k, v in raw.items()
            if k in OAuthClientInformationFull.model_fields
        }
        try:
            return OAuthClientInformationFull(**fields)
        except Exception:
            return None

    async def get_tokens(self) -> Optional[OAuthToken]:
        return self._token_from(self._load().get("tokens"))

    async def set_tokens(self, tokens: OAuthToken) -> None:
        record = self._load()
        record["tokens"] = tokens.model_dump(mode="json")
        self._save(record)

    async def get_client_info(self) -> Optional[OAuthClientInformationFull]:
        return self._client_info_from(self._load().get("client_info"))

    async def set_client_info(
        self, info: OAuthClientInformationFull
    ) -> None:
        record = self._load()
        record["client_info"] = info.model_dump(mode="json")
        self._save(record)

    def has_credentials(self) -> bool:
        """Sync check for the UI: stored tokens or a registered client."""
        record = self._load()
        if self._token_from(record.get("tokens")) is not None:
            return True
        return self._client_info_from(record.get("client_info")) is not None


class _OAuthCallbackServer:
    """One-shot 127.0.0.1 listener that receives the OAuth redirect.

    The browser is sent to `http://localhost:<port>/callback?code=...&state=...
    [&iss=...]`; the handler captures the params, answers a small "you can
    close this tab" page, and signals `wait()`. Binds an ephemeral port
    (port 0) at construction, so each authorization gets its own redirect
    URI - which is also why a stored client registration is re-used across
    runs only for its client_id: the SDK re-registers when the redirect URI
    no longer matches.
    """

    def __init__(self):
        self._event = threading.Event()
        self._params: Dict[str, str] = {}
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0), self._make_handler()
        )
        self.port = self._server.server_address[1]
        self.redirect_uri = f"http://localhost:{self.port}/callback"
        self._thread: Optional[threading.Thread] = None

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                outer._params = {
                    "code": (query.get("code") or [""])[0],
                    "state": (query.get("state") or [""])[0],
                    "iss": (query.get("iss") or [""])[0],
                }
                body = (
                    b"<html><body><h2>Authorization complete</h2>"
                    b"<p>You can close this tab and return to the app.</p>"
                    b"</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer._event.set()

            def log_message(self, *args):
                pass

        return Handler

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float) -> Optional[AuthorizationCodeResult]:
        if not self._event.wait(timeout):
            return None
        return AuthorizationCodeResult(**self._params)

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass


def extract_tool_result_text(
    result: types.CallToolResult, max_chars: Optional[int] = None
) -> str:
    """Flatten a CallToolResult into the text that re-enters model context.

    `structured_content` (JSON) is preferred when the server provides it;
    otherwise the TextContent parts are joined. The result is truncated to
    `max_chars` (config.MCP_TOOL_RESULT_MAX_CHARS by default) so one large
    payload can't eat the whole context budget.
    """
    if max_chars is None:
        max_chars = MCP_TOOL_RESULT_MAX_CHARS
    if result.structured_content is not None:
        text = json.dumps(result.structured_content, default=str)
    else:
        parts = [
            part.text for part in result.content if isinstance(part, types.TextContent)
        ]
        text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text


class McpManager:
    """Per-operation MCP access for the agent runtime and the Agents page.

    Hold one instance per vault; it keeps no connections (see module
    docstring) - only `last_auth_url` (the authorization URL most recently
    opened in a browser, for UI display).
    """

    def __init__(self, vault: Any):
        self.vault = vault
        self.last_auth_url: Optional[str] = None

    # -- public API -------------------------------------------------------

    def list_tools(self, server_id: str) -> List[McpTool]:
        """All tools of one server (fresh connection per call)."""
        server = self._require_server(server_id)
        try:
            return asyncio.run(
                self._with_session(
                    server,
                    lambda session: self._list_tools_async(session, server_id),
                    interactive=False,
                )
            )
        except Exception as exc:
            raise _to_mcp_error(exc, server.get("name", server_id)) from exc

    def call_tool(
        self, server_id: str, name: str, args: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """Execute one tool; server errors come back as ToolResult(ok=False)."""
        server = self._require_server(server_id)
        try:
            return asyncio.run(
                self._with_session(
                    server,
                    lambda session: self._call_tool_async(session, name, args or {}),
                    interactive=False,
                )
            )
        except Exception as exc:
            raise _to_mcp_error(exc, server.get("name", server_id)) from exc

    def connect(self, server_id: str, interactive: bool = False) -> None:
        """Initialize + list tools, persisting last_status/last_error/tool_count.

        `interactive=True` wires the browser OAuth flow (ephemeral callback
        listener, webbrowser.open) - used by the "Connect & Authorize"
        button. With valid stored tokens no browser opens at all: the flow
        only triggers on a 401 with nothing refreshable. A disabled server
        is marked "disabled" without any network call.
        """
        server = store.get_mcp_server(self.vault, server_id)
        if server is None:
            raise McpError(f"Unknown MCP server: {server_id}")
        if not server.get("enabled", False):
            store.update_mcp_server_status(
                self.vault, server_id, last_status="disabled", last_error=None
            )
            return
        try:
            tools = asyncio.run(
                self._with_session(
                    server,
                    lambda session: self._list_tools_async(session, server_id),
                    interactive=interactive,
                )
            )
        except Exception as exc:
            if isinstance(exc, (_NeedsAuth, OAuthFlowError)):
                store.update_mcp_server_status(
                    self.vault,
                    server_id,
                    last_status="needs_auth",
                    last_error=str(exc) or "authorization required",
                )
            else:
                message = str(exc) if isinstance(exc, McpError) else (
                    f"{type(exc).__name__}: {exc}"
                )
                store.update_mcp_server_status(
                    self.vault, server_id, last_status="error", last_error=message
                )
            return
        store.update_mcp_server_status(
            self.vault,
            server_id,
            last_status="connected",
            last_error=None,
            tool_count=len(tools),
        )

    def has_stored_credentials(self, server_id: str) -> bool:
        """For UI captions: True if tokens/client info are already in the
        vault (authorization may be silent) - False means a browser flow
        will be needed for the first connect."""
        return VaultTokenStorage(self.vault, server_id).has_credentials()

    @staticmethod
    def to_ollama_tool(tool: McpTool) -> Dict[str, Any]:
        """OpenAI-style function schema, as Ollama's `tools` expects."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema
                or {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def is_sensitive_tool(name: str) -> bool:
        """Prefix heuristic for state-mutating tools (design D5). MCP
        annotations win when a server provides them (see _tool_is_write)."""
        return any(name.startswith(prefix) for prefix in SENSITIVE_TOOL_PREFIXES)

    # -- internals ---------------------------------------------------------

    def _require_server(self, server_id: str) -> Dict[str, Any]:
        server = store.get_mcp_server(self.vault, server_id)
        if server is None:
            raise McpError(f"Unknown MCP server: {server_id}")
        if not server.get("enabled", False):
            raise McpError(
                f"MCP server '{server.get('name', server_id)}' is disabled"
            )
        return server

    async def _with_session(
        self,
        server: Dict[str, Any],
        fn: Callable[[ClientSession], Awaitable[Any]],
        *,
        interactive: bool,
    ) -> Any:
        """One fresh transport + initialized session around `fn(session)`.

        Remote servers get an httpx2 client wrapped with the SDK's OAuth
        provider (tokens from VaultTokenStorage); stdio servers spawn the
        user-specified local command. Transport + client are always closed,
        and the callback listener always stopped, even on failure.
        """
        http_client = None
        callback_server: Optional[_OAuthCallbackServer] = None
        try:
            if server.get("kind") == "remote":
                url = server.get("url") or ""
                if not url:
                    raise McpError(
                        f"MCP server '{server.get('name')}' has no url"
                    )
                if interactive:
                    callback_server = _OAuthCallbackServer()
                    callback_server.start()
                provider = self._build_provider(server, interactive, callback_server)
                http_client = create_mcp_http_client(auth=provider)
                transport = streamable_http_client(url, http_client=http_client)
            else:
                command = server.get("command") or ""
                if not command:
                    raise McpError(
                        f"MCP server '{server.get('name')}' has no command"
                    )
                params = StdioServerParameters(
                    command=command, args=list(server.get("args") or [])
                )
                transport = stdio_client(params)
            async with transport as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=_MCP_SESSION_READ_TIMEOUT_SECONDS,
                ) as session:
                    return await fn(session)
        finally:
            if http_client is not None:
                await http_client.aclose()
            if callback_server is not None:
                callback_server.stop()

    def _build_provider(
        self,
        server: Dict[str, Any],
        interactive: bool,
        callback_server: Optional[_OAuthCallbackServer],
    ) -> OAuthClientProvider:
        storage = VaultTokenStorage(self.vault, server["id"])
        if interactive:
            assert callback_server is not None

            async def redirect_handler(url: str) -> None:
                self.last_auth_url = url
                await asyncio.to_thread(webbrowser.open, url)

            async def callback_handler() -> AuthorizationCodeResult:
                result = await asyncio.to_thread(
                    callback_server.wait, MCP_OAUTH_TIMEOUT_SECONDS
                )
                if result is None:
                    raise McpError(
                        f"Timed out after {MCP_OAUTH_TIMEOUT_SECONDS}s waiting "
                        "for browser authorization"
                    )
                return result
            redirect_uri = callback_server.redirect_uri
        else:
            async def redirect_handler(url: str) -> None:
                raise _NeedsAuth()

            async def callback_handler() -> AuthorizationCodeResult:
                raise _NeedsAuth()

            redirect_uri = "http://localhost/callback"

        metadata = OAuthClientMetadata(
            client_name="Personal AI Assistant",
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            application_type="web",
        )
        return OAuthClientProvider(
            server["url"],
            metadata,
            storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

    async def _list_tools_async(
        self, session: ClientSession, server_id: str
    ) -> List[McpTool]:
        result = await session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append(
                McpTool(
                    server_id=server_id,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(
                        tool.input_schema or {"type": "object", "properties": {}}
                    ),
                    is_write=self._tool_is_write(tool),
                )
            )
        return tools

    async def _call_tool_async(
        self, session: ClientSession, name: str, args: Dict[str, Any]
    ) -> ToolResult:
        result = await session.call_tool(name, args)
        return ToolResult(
            ok=not result.is_error, text=extract_tool_result_text(result)
        )

    @staticmethod
    def _tool_is_write(tool: types.Tool) -> bool:
        """MCP's annotations.readOnlyHint is the primary read/write signal
        when present; the sensitive-prefix heuristic is the fallback for
        servers that don't annotate."""
        hint = tool.annotations.read_only_hint if tool.annotations else None
        if hint is True:
            return False
        if hint is False:
            return True
        return McpManager.is_sensitive_tool(tool.name)
