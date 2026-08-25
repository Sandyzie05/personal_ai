"""Unit tests for src.ai_engine.ollama_client (no real Ollama process involved)."""

from unittest.mock import MagicMock, patch

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
