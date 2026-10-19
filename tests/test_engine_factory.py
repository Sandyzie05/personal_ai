import unittest
from unittest.mock import patch, MagicMock
import os

from src.ai_engine.engine import (
    build_chat_client,
    build_embed_client,
    client_kwargs_from_config
)
from src.config import (
    PERSONAL_AI_LLM_ENGINE,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_CHAT_MODEL,
    PERSONAL_AI_EMBED_ENGINE,
    PERSONAL_AI_OMLX_EMBED_MODEL
)


class TestEngineFactory(unittest.TestCase):

    def setUp(self):
        # Clear any environment variables that might affect tests
        if 'PERSONAL_AI_LLM_ENGINE' in os.environ:
            del os.environ['PERSONAL_AI_LLM_ENGINE']
        if 'PERSONAL_AI_OMLX_HOST' in os.environ:
            del os.environ['PERSONAL_AI_OMLX_HOST']
        if 'PERSONAL_AI_OMLX_CHAT_MODEL' in os.environ:
            del os.environ['PERSONAL_AI_OMLX_CHAT_MODEL']
        if 'PERSONAL_AI_EMBED_ENGINE' in os.environ:
            del os.environ['PERSONAL_AI_EMBED_ENGINE']
        if 'PERSONAL_AI_OMLX_EMBED_MODEL' in os.environ:
            del os.environ['PERSONAL_AI_OMLX_EMBED_MODEL']

    @patch('src.ai_engine.engine.OllamaClient')
    def test_build_chat_client_default_ollama(self, mock_ollama_client):
        # Mock the OllamaClient
        mock_instance = MagicMock()
        mock_ollama_client.return_value = mock_instance

        client = build_chat_client()

        # Should create OllamaClient by default
        mock_ollama_client.assert_called_once_with(
            host="http://localhost:11434",
            model=None,
            temperature=0.7,
            max_tokens=None,
            context_window_tokens=None
        )

    @patch('src.ai_engine.engine.OmlxClient')
    def test_build_chat_client_omlx(self, mock_omlx_client):
        # Mock the OmlxClient
        mock_instance = MagicMock()
        mock_omlx_client.return_value = mock_instance

        client = build_chat_client(engine="omlx")

        # Should create OmlxClient with correct defaults
        mock_omlx_client.assert_called_once_with(
            host=PERSONAL_AI_OMLX_HOST,
            model=PERSONAL_AI_OMLX_CHAT_MODEL,
            temperature=0.7,
            max_tokens=None,
            context_window_tokens=None
        )

    @patch('src.ai_engine.engine.OmlxClient')
    def test_build_chat_client_custom_params(self, mock_omlx_client):
        # Mock the OmlxClient
        mock_instance = MagicMock()
        mock_omlx_client.return_value = mock_instance

        client = build_chat_client(
            engine="omlx",
            host="http://custom-host:8000",
            model="custom-model",
            temperature=0.5,
            max_tokens=1024,
            context_window_tokens=4096
        )

        # Should create OmlxClient with correct custom parameters
        mock_omlx_client.assert_called_once_with(
            host="http://custom-host:8000",
            model="custom-model",
            temperature=0.5,
            max_tokens=1024,
            context_window_tokens=4096
        )

    @patch('src.ai_engine.engine.OllamaClient')
    def test_build_embed_client_default_ollama(self, mock_ollama_client):
        # Mock the OllamaClient
        mock_instance = MagicMock()
        mock_ollama_client.return_value = mock_instance

        client = build_embed_client()

        # Should create OllamaClient for embeddings by default
        mock_ollama_client.assert_called_once_with(
            host="http://localhost:11434",
            model=None,
            temperature=0.0,
            max_tokens=None,
            context_window_tokens=2048
        )

    @patch('src.ai_engine.engine.OmlxClient')
    def test_build_embed_client_omlx(self, mock_omlx_client):
        # Mock the OmlxClient
        mock_instance = MagicMock()
        mock_omlx_client.return_value = mock_instance

        client = build_embed_client(engine="omlx")

        # Should create OmlxClient for embeddings with correct defaults
        mock_omlx_client.assert_called_once_with(
            host=PERSONAL_AI_OMLX_HOST,
            model=PERSONAL_AI_OMLX_EMBED_MODEL,
            temperature=0.0,
            max_tokens=None,
            context_window_tokens=32768  # From config for Qwen3-VL-Embedding-2B-8bit
        )

    def test_client_kwargs_from_config_old_format(self):
        # Test backward compatibility with old config format
        old_config = {
            "ollama_host": "http://old-host:11434",
            "ollama_model": "old-model",
            "temperature": 0.5,
            "max_tokens": 1024
        }

        kwargs = client_kwargs_from_config(old_config)

        # Should populate with defaults for new fields
        self.assertEqual(kwargs["engine"], "ollama")  # Default
        self.assertEqual(kwargs["host"], "http://old-host:11434")
        self.assertEqual(kwargs["model"], "old-model")
        self.assertEqual(kwargs["temperature"], 0.5)
        self.assertEqual(kwargs["max_tokens"], 1024)
        self.assertEqual(kwargs["context_window_tokens"], None)
        self.assertEqual(kwargs["embed_engine"], "ollama")  # Default
        self.assertEqual(kwargs["embed_host"], None)
        self.assertEqual(kwargs["embed_model"], None)

    def test_client_kwargs_from_config_new_format(self):
        # Test with complete new config format
        new_config = {
            "engine": "omlx",
            "llm_host": "http://custom-host:8000",
            "llm_model": "custom-chat-model",
            "embed_engine": "omlx",
            "embed_host": "http://embed-host:8000",
            "embed_model": "custom-embed-model",
            "temperature": 0.8,
            "context_window_tokens": 4096,
            "max_tokens": 2048
        }

        kwargs = client_kwargs_from_config(new_config)

        # Should populate all fields correctly
        self.assertEqual(kwargs["engine"], "omlx")
        self.assertEqual(kwargs["host"], "http://custom-host:8000")
        self.assertEqual(kwargs["model"], "custom-chat-model")
        self.assertEqual(kwargs["temperature"], 0.8)
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertEqual(kwargs["context_window_tokens"], 4096)
        self.assertEqual(kwargs["embed_engine"], "omlx")
        self.assertEqual(kwargs["embed_host"], "http://embed-host:8000")
        self.assertEqual(kwargs["embed_model"], "custom-embed-model")

    def test_client_kwargs_from_config_partial(self):
        # Test with partial config that only specifies some fields
        partial_config = {
            "engine": "omlx",
            "llm_model": "partial-model"
        }

        kwargs = client_kwargs_from_config(partial_config)

        # Should populate defaults for unspecified fields
        self.assertEqual(kwargs["engine"], "omlx")
        self.assertEqual(kwargs["host"], PERSONAL_AI_OMLX_HOST)  # Default
        self.assertEqual(kwargs["model"], "partial-model")
        self.assertEqual(kwargs["temperature"], 0.7)  # Default
        self.assertEqual(kwargs["max_tokens"], None)
        self.assertEqual(kwargs["context_window_tokens"], None)
        self.assertEqual(kwargs["embed_engine"], "ollama")  # Default
        self.assertEqual(kwargs["embed_host"], None)
        self.assertEqual(kwargs["embed_model"], None)


if __name__ == '__main__':
    unittest.main()