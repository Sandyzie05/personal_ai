"""Unit tests for src.agents.mcp (tool mapping, sensitivity, result
extraction, vault token storage, OAuth callback listener).

All offline: no MCP server is contacted. `mcp.types` objects are built
directly as fakes, and VaultTokenStorage runs against a real temp vault.
"""

import asyncio
import os
import urllib.request

import pytest
from mcp import types
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.agents.mcp import (
    McpError,
    McpManager,
    McpTool,
    ToolResult,
    VaultTokenStorage,
    _OAuthCallbackServer,
    _to_mcp_error,
    extract_tool_result_text,
)
from src.data_vault import DataVault

from src.data_vault import DataVault


def make_vault(tmp_path) -> DataVault:
    return DataVault(vault_path=str(tmp_path / "vault"), encryption_key=os.urandom(32))


def make_tool(name="get_equity_quotes", description="Get quotes", schema=None):
    return McpTool(
        server_id="srv1",
        name=name,
        description=description,
        input_schema=schema if schema is not None else {"type": "object"},
        is_write=False,
    )


class TestToOllamaTool:
    def test_maps_to_openai_style_function_schema(self):
        tool = make_tool(
            name="get_equity_quotes",
            description="Get real-time stock quotes",
            schema={"type": "object", "properties": {"symbols": {"type": "array"}}},
        )

        result = McpManager.to_ollama_tool(tool)

        assert result == {
            "type": "function",
            "function": {
                "name": "get_equity_quotes",
                "description": "Get real-time stock quotes",
                "parameters": {
                    "type": "object",
                    "properties": {"symbols": {"type": "array"}},
                },
            },
        }

    def test_missing_schema_becomes_empty_object_schema(self):
        tool = make_tool(schema={})

        result = McpManager.to_ollama_tool(tool)

        assert result["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }


class TestIsSensitiveTool:
    @pytest.mark.parametrize(
        "name",
        [
            "place_equity_order",
            "create_alert",
            "cancel_option_order",
            "update_scan_filters",
            "delete_alert",
            "add_to_watchlist",
            "remove_option_from_watchlist",
            "exercise_option",
            "follow_watchlist",
            "unfollow_watchlist",
            "mark_alerts_read",
            "decline_trade_approval",  # Robinhood labels it "read"
            "set_legend_chart_settings",  # Robinhood labels it "read"
        ],
    )
    def test_write_prefixes_are_sensitive(self, name):
        assert McpManager.is_sensitive_tool(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "get_equity_quotes",
            "get_accounts",
            "search",
            "preview_scan",
            "list_tools",
            "run_scan",
        ],
    )
    def test_read_names_are_not_sensitive(self, name):
        assert McpManager.is_sensitive_tool(name) is False


class TestToolIsWrite:
    def test_readonly_hint_true_means_read(self):
        mcp_tool = types.Tool(
            name="place_equity_order",
            description="d",
            input_schema={},
            annotations=types.ToolAnnotations(read_only_hint=True),
        )

        assert McpManager._tool_is_write(mcp_tool) is False

    def test_readonly_hint_false_means_write(self):
        mcp_tool = types.Tool(
            name="get_equity_quotes",
            description="d",
            input_schema={},
            annotations=types.ToolAnnotations(read_only_hint=False),
        )

        assert McpManager._tool_is_write(mcp_tool) is True

    def test_no_annotations_falls_back_to_prefix_heuristic(self):
        write = types.Tool(name="create_alert", description="d", input_schema={})
        read = types.Tool(name="get_equity_quotes", description="d", input_schema={})

        assert McpManager._tool_is_write(write) is True
        assert McpManager._tool_is_write(read) is False

    def test_null_hint_falls_back_to_prefix_heuristic(self):
        mcp_tool = types.Tool(
            name="delete_alert",
            description="d",
            input_schema={},
            annotations=types.ToolAnnotations(),
        )

        assert McpManager._tool_is_write(mcp_tool) is True


class TestExtractToolResultText:
    def test_structured_content_is_preferred_as_json(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="ignored text")],
            structured_content={"symbols": ["AAPL"], "price": 212.5},
        )

        text = extract_tool_result_text(result)

        assert "AAPL" in text
        assert "212.5" in text
        assert "ignored text" not in text

    def test_text_parts_are_joined_when_no_structured_content(self):
        result = types.CallToolResult(
            content=[
                types.TextContent(type="text", text="first part"),
                types.TextContent(type="text", text="second part"),
            ],
        )

        assert extract_tool_result_text(result) == "first part\nsecond part"

    def test_empty_result_yields_empty_string(self):
        result = types.CallToolResult(content=[])

        assert extract_tool_result_text(result) == ""

    def test_result_longer_than_max_chars_is_truncated_with_marker(self):
        long_text = "x" * 10_000
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text=long_text)]
        )

        text = extract_tool_result_text(result, max_chars=100)

        assert len(text) < 10_000
        assert text.endswith("[truncated]")
        assert text.startswith("x" * 100)

    def test_is_error_flag_lives_on_ToolResult_not_the_text(self):
        """call_tool wraps is_error into ToolResult.ok; the text itself is
        unchanged so the model can read the server's error message."""
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="Order rejected: PDT rule")],
            is_error=True,
        )

        tool_result = ToolResult(ok=not result.is_error,
                                 text=extract_tool_result_text(result))

        assert tool_result.ok is False
        assert "PDT rule" in tool_result.text


class TestErrorClassification:
    def test_generic_exception_becomes_plain_mcp_error(self):
        error = _to_mcp_error(ConnectionRefusedError("no route"), "Robinhood")

        assert isinstance(error, McpError)
        assert error.needs_auth is False
        assert "no route" in str(error)

    def test_oauth_flow_error_is_needs_auth(self):
        error = _to_mcp_error(OAuthFlowError("token expired"), "Robinhood")

        assert error.needs_auth is True
        assert "Robinhood" in str(error)

    def test_existing_mcp_error_passes_through(self):
        original = McpError("server is disabled")

        assert _to_mcp_error(original, "X") is original


class TestVaultTokenStorage:
    def _storage(self, tmp_path, server_id="abc123"):
        return VaultTokenStorage(make_vault(tmp_path), server_id)

    def test_roundtrip_tokens_and_client_info(self, tmp_path):
        storage = self._storage(tmp_path)
        token = OAuthToken(
            access_token="at-1", token_type="Bearer", expires_in=3600,
            scope="internal", refresh_token="rt-1",
        )
        client_info = OAuthClientInformationFull(
            client_id="client-1", redirect_uris=["http://localhost:1/callback"]
        )

        asyncio.run(storage.set_tokens(token))
        asyncio.run(storage.set_client_info(client_info))

        assert storage.has_credentials() is True
        stored_token = asyncio.run(storage.get_tokens())
        assert stored_token.access_token == "at-1"
        assert stored_token.refresh_token == "rt-1"
        assert stored_token.scope == "internal"
        stored_info = asyncio.run(storage.get_client_info())
        assert stored_info.client_id == "client-1"

    def test_missing_key_degrades_to_none(self, tmp_path):
        storage = self._storage(tmp_path, server_id="never-authorized")

        assert asyncio.run(storage.get_tokens()) is None
        assert asyncio.run(storage.get_client_info()) is None
        assert storage.has_credentials() is False

    def test_non_dict_record_degrades_to_none(self, tmp_path):
        storage = self._storage(tmp_path)
        storage.vault.store_data(storage.key, ["not", "a", "dict"])

        assert asyncio.run(storage.get_tokens()) is None
        assert storage.has_credentials() is False

    def test_malformed_token_payload_degrades_to_none(self, tmp_path):
        storage = self._storage(tmp_path)
        storage.vault.store_data(
            storage.key,
            {"tokens": {"token_type": "Bearer"}, "client_info": {"no_id": True}},
        )

        assert asyncio.run(storage.get_tokens()) is None
        assert asyncio.run(storage.get_client_info()) is None
        assert storage.has_credentials() is False

    def test_set_tokens_upserts_keeps_client_info(self, tmp_path):
        storage = self._storage(tmp_path)
        asyncio.run(
            storage.set_client_info(
                OAuthClientInformationFull(
                    client_id="c1", redirect_uris=["http://localhost:1/cb"]
                )
            )
        )
        asyncio.run(
            storage.set_tokens(
                OAuthToken(access_token="new-token", token_type="Bearer")
            )
        )

        record = storage.vault.retrieve_data(storage.key)
        assert record["tokens"]["access_token"] == "new-token"
        assert record["client_info"]["client_id"] == "c1"
        assert record["updated_at"]


class TestOAuthCallbackServer:
    @pytest.mark.loopback
    def test_redirect_params_are_captured_and_returned(self):
        server = _OAuthCallbackServer()
        server.start()
        try:
            url = (
                f"{server.redirect_uri}?code=code-123&state=st-456"
                "&iss=https%3A%2F%2Frobinhood.com"
            )
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read()
            assert response.status == 200
            assert b"Authorization complete" in body

            result = server.wait(timeout=5)

            assert result is not None
            assert result.code == "code-123"
            assert result.state == "st-456"
            assert result.iss == "https://robinhood.com"
        finally:
            server.stop()

    def test_wait_times_out_to_none(self):
        server = _OAuthCallbackServer()
        server.start()
        try:
            assert server.wait(timeout=0.2) is None
        finally:
            server.stop()

    def test_binds_ephemeral_port_on_localhost(self):
        server = _OAuthCallbackServer()

        assert server.redirect_uri.startswith("http://localhost:")
        assert server.port > 0


def test_google_root_issuer_trailing_slash_accepted_other_mismatches_rejected():
    """Gmail sign-in broke on 'https://accounts.google.com != https://accounts.google.com/' (SDK strict compare)."""
    from types import SimpleNamespace

    import pytest
    from mcp.client.auth import OAuthFlowError
    from mcp.client.auth import oauth2

    import src.agents.mcp  # noqa: F401  (installs the relaxed check)

    check = oauth2.validate_metadata_issuer
    check(SimpleNamespace(issuer="https://accounts.google.com"), "https://accounts.google.com/")
    check(SimpleNamespace(issuer="https://accounts.google.com/"), "https://accounts.google.com")
    for issuer, expected in (("https://evil.example", "https://accounts.google.com/"),
                             ("https://a.example/tenant", "https://a.example/tenant/"),
                             ("http://accounts.google.com", "https://accounts.google.com/")):
        with pytest.raises(OAuthFlowError):
            check(SimpleNamespace(issuer=issuer), expected)


def _provider(storage):
    from mcp.shared.auth import OAuthClientMetadata

    from src.agents.mcp import _VaultOAuthProvider

    async def nope(*a):
        raise AssertionError("no browser in tests")

    meta = OAuthClientMetadata(client_name="t", redirect_uris=["http://localhost/callback"],
                               token_endpoint_auth_method="none")
    return _VaultOAuthProvider("https://gmail.example/mcp", meta, storage, redirect_handler=nope, callback_handler=nope)


def test_stored_expiry_makes_an_expired_token_refresh_silently(tmp_path):
    """Before: expiry lived only in memory, so a stale token was sent and the 401 demanded a browser sign-in."""
    import time as _t

    storage = VaultTokenStorage(make_vault(tmp_path), "gm")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="a", refresh_token="r", expires_in=3600)))
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(client_id="c", redirect_uris=["http://localhost/cb"])))
    assert storage.expires_at() == pytest.approx(_t.time() + 3600, abs=5)
    p = _provider(storage)
    asyncio.run(p._initialize())
    assert p.context.is_token_valid()  # fresh: used as is
    storage.vault.store_data(storage.key, {**storage._load(), "expires_at": _t.time() - 10})
    p = _provider(storage)
    asyncio.run(p._initialize())
    assert not p.context.is_token_valid() and p.context.can_refresh_token()  # expired: refresh grant, no browser


def test_token_stored_before_the_fix_is_refreshed_first(tmp_path):
    storage = VaultTokenStorage(make_vault(tmp_path), "gm")
    rec = {"tokens": {"access_token": "a", "refresh_token": "r", "token_type": "Bearer"},
           "client_info": {"client_id": "c", "redirect_uris": ["http://localhost/cb"]}}
    storage.vault.store_data(storage.key, rec)
    p = _provider(storage)
    asyncio.run(p._initialize())
    assert not p.context.is_token_valid() and p.context.can_refresh_token()


def test_probe_call_is_one_harmless_read():
    from src.agents.mcp import McpTool, _probe_call

    tools = [McpTool("s", "trash_thread", "", {}, True), McpTool("s", "create_draft", "", {}, False),
             McpTool("s", "get_thread", "", {"properties": {"threadId": {}}, "required": ["threadId"]}, False),
             McpTool("s", "search_threads", "", {"properties": {"query": {}, "pageSize": {}}, "required": ["query"]}, False)]
    assert _probe_call(tools) == ("search_threads", {"query": "newer_than:1d", "pageSize": 1})
    assert _probe_call(tools[:3]) is None  # write / sensitive / needs an id: never probed


def test_probe_reports_listing_and_read_call(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.agents import store
    from src.agents.mcp import McpManager, McpTool, ToolResult

    vault = make_vault(tmp_path)
    sid = store.save_mcp_server(vault, {"name": "Gmail", "kind": "remote", "url": "https://x.example/mcp", "enabled": True})
    m = McpManager(vault)
    calls = []

    async def fake_with_session(server, fn, *, interactive):
        assert interactive
        return await fn(None)

    async def fake_list(session, server_id):
        return [McpTool(server_id, "search_threads", "", {"properties": {"query": {}}}, False),
                McpTool(server_id, "trash_thread", "", {}, True)]

    async def fake_call(session, name, args, max_chars=None):
        calls.append(name)
        return ToolResult(ok=True, text="{}")

    monkeypatch.setattr(m, "_with_session", fake_with_session)
    monkeypatch.setattr(m, "_list_tools_async", fake_list)
    monkeypatch.setattr(m, "_call_tool_async", fake_call)
    r = m.probe(sid)
    assert r["listed"] and (r["read"], r["write"]) == (1, 1) and r["probe_ok"] and calls == ["search_threads"]
    assert store.get_mcp_server(vault, sid)["last_status"] == "connected"


def test_registration_404_gives_own_client_hint_without_server_html():
    from mcp.client.auth.exceptions import OAuthRegistrationError

    from src.agents.mcp import _to_mcp_error

    e = _to_mcp_error(BaseExceptionGroup("tg", [OAuthRegistrationError("Registration failed: 404 <!DOCTYPE html>")]), "Gmail")
    assert e.needs_auth and "own OAuth client" in str(e) and "<" not in str(e)


def test_preregistered_client_is_used_and_scope_pinned_read_only(tmp_path):
    from src.agents.mcp import GMAIL_READONLY_SCOPE, _google_offline, _VaultOAuthProvider, server_scope
    from mcp.shared.auth import OAuthClientMetadata

    storage = VaultTokenStorage(make_vault(tmp_path), "gm")
    storage.set_preregistered_client("abc.apps.googleusercontent.com", "s3cret")
    assert storage.preregistered_client_id() == "abc.apps.googleusercontent.com"
    assert server_scope({"name": "Gmail"}) == GMAIL_READONLY_SCOPE and server_scope({"name": "Robinhood"}) is None
    assert server_scope({"name": "Gmail", "oauth_scope": "x"}) == "x"
    seen = []

    class Stop(Exception):
        pass

    async def redirect(url):
        seen.append(url)
        raise Stop()

    async def cb():
        raise Stop()

    meta = OAuthClientMetadata(client_name="t", redirect_uris=["http://localhost:1/callback"], token_endpoint_auth_method="none")
    p = _VaultOAuthProvider("https://gmailmcp.googleapis.com/mcp/v1", meta, storage, redirect_handler=redirect,
                            callback_handler=cb, pinned_scope=GMAIL_READONLY_SCOPE)
    asyncio.run(p._initialize())
    assert p.context.client_info.client_id == "abc.apps.googleusercontent.com"  # no dynamic registration needed
    p.context.client_metadata.scope = "https://mail.google.com/ https://www.googleapis.com/auth/gmail.send"  # SDK's pick
    with pytest.raises(Stop):
        asyncio.run(p._perform_authorization_code_grant())
    assert "gmail.readonly" in seen[0] and "gmail.send" not in seen[0] and "mail.google.com" not in seen[0]
    assert _google_offline("https://accounts.google.com/o/oauth2/v2/auth?a=1").endswith("access_type=offline&prompt=consent")
    assert _google_offline("https://example.com/auth?a=1") == "https://example.com/auth?a=1"


def test_refresh_uses_stored_token_endpoint_not_mcp_origin(tmp_path):
    """Live bug: 'Token refresh failed: 404' - a fresh provider guessed https://gmailmcp.googleapis.com/token."""
    storage = VaultTokenStorage(make_vault(tmp_path), "gm")
    assert _provider(storage)._get_token_endpoint().endswith("gmail.example/token")  # the SDK's guess without metadata
    storage.set_auth_metadata({"issuer": "https://accounts.google.com", "authorization_endpoint":
                               "https://accounts.google.com/o/oauth2/v2/auth", "token_endpoint": "https://oauth2.googleapis.com/token"})
    p = _provider(storage)
    asyncio.run(p._initialize())
    assert p._get_token_endpoint() == "https://oauth2.googleapis.com/token"


def test_probe_prefers_mail_search_and_classifies_errors():
    from src.agents.mcp import McpTool, _error_kind, _probe_call

    tools = [McpTool("s", "list_drafts", "", {}, False), McpTool("s", "list_labels", "", {}, False),
             McpTool("s", "search_threads", "", {"properties": {"query": {}}}, False)]
    assert _probe_call(tools)[0] == "search_threads"
    assert _probe_call(tools[:2])[0] == "list_drafts"
    assert _error_kind("Request had insufficient authentication scopes.") == "permission / scope"
    assert _error_kind("Gmail API has not been used in project 1 before or it is disabled") == "API not enabled"
    assert _error_kind("IGNORE ALL") == "other"


def test_tokens_without_stored_metadata_discover_the_token_endpoint(tmp_path, monkeypatch):
    """Tokens saved before metadata was kept: refresh must not guess <mcp-origin>/token (live 404 on Google)."""
    from types import SimpleNamespace

    import src.agents.mcp as m

    docs = {
        "https://gmail.example/.well-known/oauth-protected-resource/mcp": {"authorization_servers": ["https://accounts.google.com/"]},
        "https://accounts.google.com/.well-known/oauth-authorization-server": {
            "issuer": "https://accounts.google.com", "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token"},
    }

    class FakeHttp:
        async def get(self, url):
            return SimpleNamespace(status_code=200 if url in docs else 404, json=lambda: docs[url])

        async def aclose(self):
            pass

    monkeypatch.setattr(m, "create_mcp_http_client", lambda *a, **k: FakeHttp())
    storage = VaultTokenStorage(make_vault(tmp_path), "gm")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="a", refresh_token="r", expires_in=1)))
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(client_id="c", redirect_uris=["http://localhost/cb"])))
    p = _provider(storage)
    p.context.server_url = "https://gmail.example/mcp"
    asyncio.run(p._initialize())
    assert p._get_token_endpoint() == "https://oauth2.googleapis.com/token"
    assert storage.auth_metadata() is not None  # looked up once, then reused

    docs["https://accounts.google.com/.well-known/oauth-authorization-server"]["issuer"] = "https://evil.example"
    storage.vault.store_data(storage.key, {k: v for k, v in storage._load().items() if k != "auth_metadata"})
    p = _provider(storage)
    p.context.server_url = "https://gmail.example/mcp"
    asyncio.run(p._initialize())
    assert p.context.oauth_metadata is None  # issuer mismatch: not trusted
