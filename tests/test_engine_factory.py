import unittest
from unittest.mock import patch, MagicMock
import importlib

import pytest

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

    def test_missing_saved_config_uses_defaults(self):
        self.assertEqual(client_kwargs_from_config(None), client_kwargs_from_config({}))

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


@pytest.fixture
def interface_module():
    with patch("dotenv.load_dotenv"):
        module = importlib.import_module("src.interface.main")
    with patch.object(module, "st") as ui:
        ui.session_state = {}
        yield module


@pytest.mark.parametrize("initializer", ["_get_chat_engine_instance", "_initialize_rag"])
@pytest.mark.parametrize("saved", [None, {
    "engine": "omlx", "llm_host": "http://127.0.0.1:8010",
    "llm_model": "synthetic-chat", "temperature": 0.2, "max_tokens": 321,
    "context_window_tokens": 4096,
    "embed_engine": "omlx", "embed_host": "http://127.0.0.1:8020",
    "embed_model": "synthetic-embedding",
}])
def test_interface_initializers_preserve_chat_settings(interface_module, initializer, saved):
    app = interface_module.PersonalAIInterface()
    app.vault = MagicMock()
    app.vault.retrieve_data.return_value = saved
    app.encryption_key = b"synthetic-key"
    with patch("src.ai_engine.engine.build_chat_client") as build_client, \
            patch("src.ai_engine.engine.build_embed_client") as build_embedding, \
            patch("src.ai_engine.embeddings.EmbeddingsGenerator") as embeddings, \
            patch("src.ai_engine.chat_engine.ChatEngine") as engine:
        if initializer == "_get_chat_engine_instance":
            app._get_chat_engine_instance("synthetic-vault", app.encryption_key)
        else:
            app._initialize_rag()
        expected = {
            k: v for k, v in client_kwargs_from_config(app.ollama_config).items()
            if not k.startswith("embed_")
        }
        build_client.assert_called_once_with(**expected)
        kwargs = client_kwargs_from_config(app.ollama_config)
        build_embedding.assert_called_once_with(
            engine=kwargs["embed_engine"], host=kwargs["embed_host"],
            model=kwargs["embed_model"],
        )
        embeddings.assert_called_once_with(client=build_embedding.return_value)
        assert engine.call_args.kwargs["ollama_client"] is build_client.return_value
        assert engine.call_args.kwargs["embedding_generator"] is embeddings.return_value


def test_interface_fresh_defaults_match_selected_backends(interface_module):
    app = interface_module.PersonalAIInterface()
    with patch.object(interface_module, "PERSONAL_AI_LLM_ENGINE", "omlx"), \
            patch.object(interface_module, "PERSONAL_AI_EMBED_ENGINE", "omlx"):
        app._load_ollama_config()
    assert app.ollama_config["llm_host"] == PERSONAL_AI_OMLX_HOST
    assert app.ollama_config["llm_model"] == PERSONAL_AI_OMLX_CHAT_MODEL
    assert app.ollama_config["embed_host"] == PERSONAL_AI_OMLX_HOST
    assert app.ollama_config["embed_model"] == PERSONAL_AI_OMLX_EMBED_MODEL


def test_interface_fallback_preserves_generation_settings(interface_module):
    app = interface_module.PersonalAIInterface()
    app.ollama_config = {
        "engine": "omlx", "llm_host": "http://127.0.0.1:8010",
        "llm_model": "synthetic-chat", "temperature": 0.1, "max_tokens": 123,
        "context_window_tokens": 4096,
    }
    with patch("src.ai_engine.engine.build_chat_client") as build_client:
        build_client.return_value.chat.return_value = {"message": {"content": "synthetic"}}
        assert app._get_fallback_response("synthetic question")[0] == "synthetic"
    build_client.assert_called_once_with(
        engine="omlx", host="http://127.0.0.1:8010", model="synthetic-chat",
        temperature=0.1, max_tokens=123, context_window_tokens=4096,
    )


@pytest.mark.parametrize("save", [True, False])
def test_settings_save_invalidates_live_engines_only_after_saving(interface_module, save):
    app = interface_module.PersonalAIInterface()
    app.vault = MagicMock()
    app.vault.retrieve_data.return_value = {"engine": "ollama"}
    app._initialized = True
    old_engine = app.chat_engine = object()
    old_runtime = interface_module.st.session_state["agent_runtime"] = object()
    interface_module.st.button.return_value = save
    settings = {"engine": "omlx", "llm_model": "synthetic-chat"}
    with patch("src.interface.config.render_config_page", return_value=settings):
        app.render_config_page()
    if save:
        app.vault.store_data.assert_called_once_with("ollama_config", settings)
        assert app.chat_engine is None
        assert "agent_runtime" not in interface_module.st.session_state
    else:
        app.vault.store_data.assert_not_called()
        assert app.chat_engine is old_engine
        assert interface_module.st.session_state["agent_runtime"] is old_runtime


def test_settings_failed_save_keeps_live_engines(interface_module):
    app = interface_module.PersonalAIInterface()
    app.vault = MagicMock()
    app.vault.store_data.side_effect = RuntimeError("synthetic save failure")
    app._initialized = True
    old_engine = app.chat_engine = object()
    old_runtime = interface_module.st.session_state["agent_runtime"] = object()
    interface_module.st.button.return_value = True
    with patch("src.interface.config.render_config_page", return_value={"engine": "omlx"}), \
            pytest.raises(RuntimeError, match="synthetic save failure"):
        app.render_config_page()
    assert app.chat_engine is old_engine
    assert interface_module.st.session_state["agent_runtime"] is old_runtime


if __name__ == '__main__':
    unittest.main()