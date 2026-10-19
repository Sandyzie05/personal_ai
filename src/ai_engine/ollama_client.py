"""Ollama API client for local LLM interactions."""

import ollama
from typing import List, Dict, Any, Optional, Union

from src.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_CHAT_TEMPERATURE,
)


class OllamaClientError(Exception):
    """Custom exception for Ollama client errors."""

    pass


class OllamaClient:
    """Client for interacting with Ollama local LLMs."""

    def __init__(
        self,
        host: str = DEFAULT_OLLAMA_HOST,
        model: str = DEFAULT_CHAT_MODEL,
        embed_model: str = DEFAULT_EMBED_MODEL,
        temperature: float = DEFAULT_CHAT_TEMPERATURE,
        max_tokens: int = 2048,
        context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    ):
        self.host = host
        self.model = model
        self.embed_model = embed_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # The context window this client tells Ollama to actually use
        # (via `num_ctx` below). ChatEngine reads this back so its own
        # budget math can never drift from what's really sent to Ollama.
        self.context_window_tokens = context_window_tokens
        self.client = ollama.Client(host=host)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        format: Optional[Union[str, Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a chat message to Ollama.

        `format` accepts "json" or a JSON schema dict (e.g.
        `SomePydanticModel.model_json_schema()`) to constrain the model to
        structured output - used by src/data_extraction/extractor.py.

        `tools` (OpenAI-style function schemas) enables Ollama's native
        tool calling for models that support it; the response message then
        may carry `tool_calls` and follow-up messages use `role: "tool"`.
        Only passed when non-empty, so tool-less calls are byte-identical
        to before.
        """
        response = self.client.chat(
            model=self.model,
            messages=messages,
            stream=stream,
            format=format,
            tools=tools or None,
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                "num_ctx": self.context_window_tokens,
            },
        )
        return response

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a text string."""
        response = self.client.embeddings(model=self.embed_model, prompt=text)
        return response["embedding"]

    def list_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            response = self.client.list()
            # Handle Model objects from ollama library
            if hasattr(response, "models"):
                return [
                    model.model for model in response.models if hasattr(model, "model")
                ]
            elif isinstance(response, dict):
                return [model.get("name", "") for model in response.get("models", [])]
            return []
        except Exception as e:
            print(f"Error listing models: {e}")
            return []

    def model_details(self) -> Dict[str, Dict[str, Any]]:
        """Map model name -> details dict (capabilities, context_length,
        family, ...) as reported by Ollama's /api/tags. Empty dict on any
        failure - callers treat "unknown" as "no information"."""
        try:
            response = self.client.list()
            details: Dict[str, Dict[str, Any]] = {}
            for model in getattr(response, "models", None) or []:
                name = getattr(model, "model", None)
                raw = getattr(model, "details", None)
                if not name or not isinstance(raw, dict):
                    continue
                details[name] = dict(raw)
            return details
        except Exception:
            return {}

    def model_capabilities(self) -> Dict[str, List[str]]:
        """Map model name -> capability list (e.g. ["tools", "vision"]) as
        reported by Ollama's /api/tags. Empty dict on any failure - callers
        treat "unknown" as "no tools" and surface a notice rather than
        guessing."""
        return {
            name: [str(c) for c in (details.get("capabilities") or [])]
            for name, details in self.model_details().items()
        }

    def model_supports_tools(self, model_name: str) -> bool:
        return "tools" in self.model_capabilities().get(model_name, [])

    def check_connection(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            self.client.list()
            return True
        except Exception:
            return False

    def model_exists(self, model_name: str) -> bool:
        """Check if a model exists in Ollama."""
        try:
            models = self.list_models()
            return model_name in models
        except Exception:
            return False

    def pull_model(self, model_name: str) -> None:
        """Pull a model from Ollama."""
        try:
            self.client.pull(model_name)
        except Exception as e:
            raise OllamaClientError(f"Failed to pull model {model_name}: {str(e)}")
