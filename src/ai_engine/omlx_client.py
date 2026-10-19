import json
import urllib.request
import urllib.error
import os
import math
from typing import List, Dict, Any, Optional, Generator
from urllib.parse import urljoin

from .ollama_client import OllamaClientError
from ..config import PERSONAL_AI_OMLX_HOST, PERSONAL_AI_OMLX_CHAT_MODEL


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OmlxClient:
    """Client for oMLX server speaking OpenAI-compatible HTTP API."""

    def __init__(self, host: str = PERSONAL_AI_OMLX_HOST,
                 model: str = PERSONAL_AI_OMLX_CHAT_MODEL,
                 temperature: float = 0.7, max_tokens: int = None,
                 context_window_tokens: int = None,
                 request_timeout_seconds: Optional[float] = None,
                 safe_local_transport: bool = False):
        self.host = host
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.context_window_tokens = context_window_tokens
        if request_timeout_seconds is not None and (
            not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0
        ):
            raise ValueError("Request timeout must be finite and positive")
        if safe_local_transport and request_timeout_seconds is None:
            raise ValueError("Safe local transport requires a finite request timeout")
        self.request_timeout_seconds = request_timeout_seconds
        self._opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            if safe_local_transport else None
        )
        self._supports_tools = False  # oMLX doesn't currently support tools
        # Read API key from environment
        self.api_key = os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY")

    @property
    def embed_model(self) -> str:
        return self.model

    def _open(self, request):
        if self._opener is not None:
            return self._opener.open(request, timeout=self.request_timeout_seconds)
        if self.request_timeout_seconds is not None:
            return urllib.request.urlopen(request, timeout=self.request_timeout_seconds)
        return urllib.request.urlopen(request)

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Make HTTP request to oMLX server."""
        url = urljoin(self.host, endpoint)
        headers = {"Content-Type": "application/json"}

        # Add API key header if available
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if data:
            json_data = json.dumps(data).encode('utf-8')
        else:
            json_data = None

        req = urllib.request.Request(url, data=json_data, headers=headers, method=method)

        try:
            with self._open(req) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise OllamaClientError(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise OllamaClientError(f"URL Error: {str(e)}")

    def chat(self, messages: List[Dict], stream: bool = False, format: Optional[str] = None,
             tools: Optional[List[Dict]] = None) -> Any:
        """Send chat request to oMLX server."""
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
        }

        if self.max_tokens is not None:
            data["max_tokens"] = self.max_tokens

        if format:
            if format == "json":
                data["response_format"] = {"type": "json_object"}
            else:
                # For other format types, we'll try to use json_schema
                data["response_format"] = {"type": "json_schema", "schema": format}

        if tools:
            data["tools"] = tools

        if not stream:
            # Non-streaming response
            response = self._make_request("POST", "/v1/chat/completions", data)
            # Convert to Ollama format
            message = response["choices"][0]["message"]
            return {"message": message}
        else:
            # Streaming response - parse SSE
            return self._stream_chat_response(data)

    def _stream_chat_response(self, data: Dict) -> Generator[Dict, None, None]:
        """Parse streaming chat response."""
        url = urljoin(self.host, "/v1/chat/completions")
        headers = {"Content-Type": "application/json"}
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")

        try:
            with self._open(req) as response:
                for line in response:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data_line = line[6:]  # Remove 'data: '
                        if data_line.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_line)
                            delta = chunk["choices"][0]["delta"]
                            yield {"message": delta}
                        except json.JSONDecodeError:
                            continue  # Skip malformed lines
        except Exception as e:
            raise OllamaClientError(f"Streaming error: {str(e)}")

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using oMLX."""
        data = {
            "model": self.embed_model,
            "input": [text]
        }

        response = self._make_request("POST", "/v1/embeddings", data)
        return response["data"][0]["embedding"]

    def list_models(self) -> List[str]:
        """List available models on oMLX server."""
        response = self._make_request("GET", "/v1/models")
        return [model["id"] for model in response["data"]]

    def model_details(self) -> Dict[str, Any]:
        """Get detailed model information."""
        return {}

    def model_capabilities(self) -> Dict[str, Any]:
        """Get model capabilities."""
        return {}

    def model_supports_tools(self) -> bool:
        """Check if model supports tools."""
        return False

    def check_connection(self) -> bool:
        """Check if oMLX server is reachable."""
        try:
            self._make_request("GET", "/v1/models")
            return True
        except Exception as e:
            # Print the error for debugging
            print(f"Connection check failed: {e}")
            return False

    def model_exists(self, name: str) -> bool:
        """Check if model exists."""
        try:
            models = self.list_models()
            # Check exact match and with tag suffix
            return name in models or any(model.startswith(name + ":") for model in models)
        except Exception:
            # For model existence checking, return False if we can't connect
            # This allows the system to proceed rather than crash
            return False

    def pull_model(self, name: str) -> None:
        """Pull model (unsupported in oMLX, raise error with instruction)."""
        raise OllamaClientError(f"oMLX models must be loaded via the omlx CLI. "
                               f"Run `omlx load {name}` to load this model.")