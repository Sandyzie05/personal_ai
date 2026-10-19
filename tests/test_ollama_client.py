"""Unit tests for src.ai_engine.ollama_client (no real Ollama process involved)."""

from unittest.mock import MagicMock, patch

import httpx
import ollama
import pytest

from src.ai_engine.ollama_client import OllamaClient
from src.config import DEFAULT_CONTEXT_WINDOW_TOKENS


def _make_client(**kwargs) -> OllamaClient:
    with patch("src.ai_engine.ollama_client.ollama.Client") as mock_ctor:
        mock_ctor.return_value = MagicMock()
        client = OllamaClient(**kwargs)
    return client


def test_context_window_tokens_defaults_to_config_value():
    client = _make_client()

    assert client.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS


def test_context_window_tokens_constructor_override():
    client = _make_client(context_window_tokens=2048)

    assert client.context_window_tokens == 2048


def test_chat_passes_num_ctx_matching_context_window_tokens():
    client = _make_client(context_window_tokens=4096)
    client.client.chat.return_value = {"message": {"content": "hi"}}

    client.chat(messages=[{"role": "user", "content": "hello"}])

    _, kwargs = client.client.chat.call_args
    assert kwargs["options"]["num_ctx"] == 4096


def test_chat_num_ctx_uses_default_when_not_overridden():
    client = _make_client()
    client.client.chat.return_value = {"message": {"content": "hi"}}

    client.chat(messages=[{"role": "user", "content": "hello"}])

    _, kwargs = client.client.chat.call_args
    assert kwargs["options"]["num_ctx"] == DEFAULT_CONTEXT_WINDOW_TOKENS


def test_chat_also_passes_temperature_and_num_predict():
    client = _make_client(temperature=0.3, max_tokens=512)
    client.client.chat.return_value = {"message": {"content": "hi"}}

    client.chat(messages=[{"role": "user", "content": "hello"}])

    _, kwargs = client.client.chat.call_args
    assert kwargs["options"]["temperature"] == 0.3
    assert kwargs["options"]["num_predict"] == 512


def test_safe_transport_sets_finite_timeouts_and_disables_redirects_and_proxies():
    with patch("src.ai_engine.ollama_client.ollama.Client") as constructor:
        OllamaClient(request_timeout_seconds=.25, safe_local_transport=True)
    options = constructor.call_args.kwargs
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    timeout = options["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (.25,) * 4


@pytest.mark.parametrize("timeout", [None, 0, -1, float("inf"), float("nan")])
def test_safe_transport_rejects_unbounded_or_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        OllamaClient(request_timeout_seconds=timeout, safe_local_transport=True)


@pytest.mark.parametrize("status", [307, 308])
def test_actual_sdk_does_not_forward_redirected_evidence(status, monkeypatch):
    sdk = ollama.Client
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://remote.invalid/evidence"})

    monkeypatch.setattr(
        "src.ai_engine.ollama_client.ollama.Client",
        lambda **kwargs: sdk(transport=httpx.MockTransport(respond), **kwargs),
    )
    client = OllamaClient(
        host="http://127.0.0.1:11434", model="synthetic",
        request_timeout_seconds=.25, safe_local_transport=True,
    )
    with pytest.raises(ollama.ResponseError):
        client.chat([{"role": "user", "content": "synthetic public evidence"}])
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"


def test_actual_sdk_stalled_transport_receives_remaining_budget(monkeypatch):
    sdk = ollama.Client

    def stalled(request):
        assert request.extensions["timeout"] == {
            "connect": .125, "read": .125, "write": .125, "pool": .125,
        }
        raise httpx.ReadTimeout("synthetic stalled read", request=request)

    monkeypatch.setattr(
        "src.ai_engine.ollama_client.ollama.Client",
        lambda **kwargs: sdk(transport=httpx.MockTransport(stalled), **kwargs),
    )
    client = OllamaClient(request_timeout_seconds=.125, safe_local_transport=True)
    with pytest.raises(httpx.ReadTimeout):
        client.chat([{"role": "user", "content": "synthetic"}])
