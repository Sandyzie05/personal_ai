import unittest
from unittest.mock import patch, MagicMock

from src.ai_engine.engine import (
    build_chat_client,
    build_embed_client,
    client_kwargs_from_config
)
from src.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    EMBED_MODEL_CONTEXT_TOKENS,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_CHAT_MODEL,
    PERSONAL_AI_OMLX_EMBED_MODEL
)


class TestEngineFactory(unittest.TestCase):

    def setUp(self):
        # These cases cover the (opt-in) Ollama path; disabled-path tests live below.
        patcher = patch("src.ai_engine.engine.OLLAMA_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("PERSONAL_AI_LLM_ENGINE", "PERSONAL_AI_EMBED_ENGINE"):
            patcher = patch(f"src.ai_engine.engine.{name}", "ollama")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_safe_transport_options_forwarded_to_both_backends(self):
        for backend, class_name in (("ollama", "OllamaClient"), ("omlx", "OmlxClient")):
            with self.subTest(backend=backend), patch(
                f"src.ai_engine.engine.{class_name}"
            ) as constructor:
                build_chat_client(
                    engine=backend, request_timeout_seconds=.5,
                    safe_local_transport=True,
                )
                self.assertEqual(constructor.call_args.kwargs["request_timeout_seconds"], .5)
                self.assertIs(constructor.call_args.kwargs["safe_local_transport"], True)

    @patch('src.ai_engine.engine.OllamaClient')
    def test_build_chat_client_default_ollama(self, mock_ollama_client):
        # Mock the OllamaClient
        mock_instance = MagicMock()
        mock_ollama_client.return_value = mock_instance

        client = build_chat_client()

        # Should create OllamaClient by default
        mock_ollama_client.assert_called_once_with(
            host=DEFAULT_OLLAMA_HOST,
            model=DEFAULT_CHAT_MODEL,
            temperature=0.7,
            max_tokens=None,
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS
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
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS
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
            host=DEFAULT_OLLAMA_HOST,
            model=DEFAULT_EMBED_MODEL,
            embed_model=DEFAULT_EMBED_MODEL,
            temperature=0.0,
            max_tokens=None,
            context_window_tokens=EMBED_MODEL_CONTEXT_TOKENS.get(DEFAULT_EMBED_MODEL, 2048)
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
            context_window_tokens=EMBED_MODEL_CONTEXT_TOKENS.get(PERSONAL_AI_OMLX_EMBED_MODEL, 2048)
        )

    @patch('src.ai_engine.engine.OmlxClient')
    def test_configured_omlx_defaults_select_both_clients(self, mock_omlx_client):
        with patch('src.ai_engine.engine.PERSONAL_AI_LLM_ENGINE', "omlx"), \
                patch('src.ai_engine.engine.PERSONAL_AI_EMBED_ENGINE', "omlx"):
            build_chat_client()
            self.assertEqual(mock_omlx_client.call_args.kwargs["model"],
                             PERSONAL_AI_OMLX_CHAT_MODEL)
            build_embed_client()
            self.assertEqual(mock_omlx_client.call_args.kwargs["model"],
                             PERSONAL_AI_OMLX_EMBED_MODEL)
        self.assertEqual(mock_omlx_client.call_count, 2)

    @patch('src.ai_engine.ollama_client.ollama.Client')
    def test_native_ollama_embedding_uses_selected_model(self, mock_sdk):
        mock_sdk.return_value.embeddings.return_value = {"embedding": [0.1, 0.2]}
        client = build_embed_client(
            engine="ollama", host="http://embed-host:11434",
            model="custom-embedding-model"
        )

        self.assertEqual(client.generate_embedding("example"), [0.1, 0.2])
        mock_sdk.assert_called_once_with(host="http://embed-host:11434")
        mock_sdk.return_value.embeddings.assert_called_once_with(
            model="custom-embedding-model", prompt="example"
        )

    def test_config_keys_precedence_and_backend_fallbacks(self):
        legacy = {"ollama_host": "http://legacy-host:11434",
                  "ollama_model": "legacy-model"}
        kwargs = client_kwargs_from_config({
            **legacy, "llm_host": "http://new-host:11434", "llm_model": "new-model",
        })
        self.assertEqual(kwargs["host"], "http://new-host:11434")
        self.assertEqual(kwargs["model"], "new-model")

        kwargs = client_kwargs_from_config({
            **legacy, "llm_host": None, "llm_model": None,
        })
        self.assertEqual(kwargs["host"], legacy["ollama_host"])
        self.assertEqual(kwargs["model"], legacy["ollama_model"])

        kwargs = client_kwargs_from_config({**legacy, "engine": "omlx"})
        self.assertEqual(kwargs["host"], PERSONAL_AI_OMLX_HOST)
        self.assertEqual(kwargs["model"], PERSONAL_AI_OMLX_CHAT_MODEL)

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

class TestOllamaDisabled(unittest.TestCase):
    def setUp(self):
        for name, val in (("OLLAMA_ENABLED", False), ("PERSONAL_AI_LLM_ENGINE", "omlx"),
                          ("PERSONAL_AI_EMBED_ENGINE", "omlx")):
            p = patch(f"src.ai_engine.engine.{name}", val)
            p.start()
            self.addCleanup(p.stop)

    def test_explicit_ollama_raises(self):
        from src.ai_engine.engine import EngineDisabledError
        with self.assertRaises(EngineDisabledError):
            build_chat_client(engine="ollama")
        with self.assertRaises(EngineDisabledError):
            build_embed_client(engine="ollama")

    def test_defaults_build_omlx(self):
        self.assertEqual(type(build_chat_client()).__name__, "OmlxClient")
        self.assertEqual(type(build_embed_client()).__name__, "OmlxClient")

    def test_saved_ollama_config_migrates_and_drops_ollama_host_model(self):
        kw = client_kwargs_from_config({
            "engine": "ollama", "llm_host": "http://localhost:11434", "llm_model": "llama3.2:latest",
            "embed_engine": "ollama", "embed_model": "nomic-embed-text",
        })
        self.assertEqual((kw["engine"], kw["embed_engine"]), ("omlx", "omlx"))
        self.assertIsNone(kw["host"]); self.assertIsNone(kw["model"]); self.assertIsNone(kw["embed_model"])
        client = build_chat_client(**{k: kw[k] for k in ("engine", "host", "model")})
        self.assertEqual(client.model, PERSONAL_AI_OMLX_CHAT_MODEL)
