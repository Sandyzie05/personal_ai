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
