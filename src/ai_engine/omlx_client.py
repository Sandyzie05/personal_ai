import json
import urllib.request
import urllib.error
import os
import math
import uuid
from typing import List, Dict, Any, Optional, Generator
from ipaddress import ip_address
from urllib.parse import urljoin, urlparse

from .ollama_client import OllamaClientError
from ..config import PERSONAL_AI_OMLX_HOST, PERSONAL_AI_OMLX_CHAT_MODEL, OMLX_SUPPORTS_TOOLS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_loopback(host: str) -> bool:
    name = urlparse(host).hostname or ""
    try:
        return ip_address(name).is_loopback
    except ValueError:
        return name == "localhost"


def _remote_allowed() -> bool:
    return os.environ.get("PERSONAL_AI_ALLOW_REMOTE_OMLX", "").lower() in ("1", "true", "yes")


def _to_openai_messages(messages: List[Dict]) -> List[Dict]:
    """Ollama-shaped history (dict tool args, id-less tool results) -> OpenAI shape.

    The agent runtime speaks Ollama's shape; ids are minted here and matched to
    the following tool results by order/name so the runtime stays engine-agnostic."""
    out: List[Dict] = []
    pending: List[Dict] = []
    for m in messages:
        m = dict(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function", {})
                args = fn.get("arguments", {})
                calls.append({
                    "id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": fn.get("name", ""),
                                 "arguments": args if isinstance(args, str) else json.dumps(args)},
                })
            m["tool_calls"] = calls
            m["content"] = m.get("content") or ""
            pending = list(calls)
        elif m.get("role") == "tool":
            idx = next((i for i, c in enumerate(pending)
                        if c["function"]["name"] == m.get("name")), 0 if pending else None)
            if idx is None:  # orphan result: an invented id would be rejected by the server
                out.append({"role": "user",
                            "content": f"Tool result ({m.get('name', 'tool')}): {m.get('content', '')}"})
                continue
            m["tool_call_id"] = pending.pop(idx)["id"]
            m.pop("name", None)
        out.append(m)
    return out


def _from_openai_message(message: Dict) -> Dict:
    """Parse JSON-string tool arguments into dicts (Ollama shape); malformed -> {}."""
    calls = message.get("tool_calls")
    if not calls:
        return message
    fixed = []
    for c in calls:
        fn = dict(c.get("function", {}))
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        fn["arguments"] = args if isinstance(args, dict) else {}
        fixed.append({**c, "function": fn})
    return {**message, "tool_calls": fixed}


class OmlxClient:
    """Client for oMLX server speaking OpenAI-compatible HTTP API."""

    def __init__(self, host: str = PERSONAL_AI_OMLX_HOST,
                 model: str = PERSONAL_AI_OMLX_CHAT_MODEL,
                 temperature: float = 0.7, max_tokens: int = None,
                 context_window_tokens: int = None,
                 request_timeout_seconds: Optional[float] = None,
                 safe_local_transport: bool = False):
        # Vault content goes to this host as RAG context and the bearer token goes with it: loopback only,
        # unless the user explicitly opts in (PERSONAL_AI_ALLOW_REMOTE_OMLX=true).
        if not _is_loopback(host) and not _remote_allowed():
            raise OllamaClientError("oMLX host must be loopback (set PERSONAL_AI_ALLOW_REMOTE_OMLX=true to override).")
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
        self._supports_tools = OMLX_SUPPORTS_TOOLS
        # Read API key from environment
        self.api_key = os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY")

    @property
    def embed_model(self) -> str:
        return self.model

    def _open(self, request):
        opener = self._opener
        if opener is None and self.api_key:  # never let a redirect/proxy see the bearer token
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        if opener is not None:
            return opener.open(request, timeout=self.request_timeout_seconds)
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
            "messages": _to_openai_messages(messages),
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
                schema = format if isinstance(format, dict) else json.loads(format)
                data["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "response", "schema": schema}}

        if tools:
            if not self._supports_tools:
                raise OllamaClientError("oMLX tool calling is disabled (PERSONAL_AI_OMLX_TOOLS=false)")
            data["tools"] = tools

        if not stream:
            # Non-streaming response
            response = self._make_request("POST", "/v1/chat/completions", data)
            # Convert to Ollama format
            choices = response.get("choices") or []
            if not choices:
                raise OllamaClientError("oMLX returned no choices")
            return {"message": _from_openai_message(choices[0]["message"])}
        else:
            # Streaming response - parse SSE
            return self._stream_chat_response(data)

    def _stream_chat_response(self, data: Dict) -> Generator[Dict, None, None]:
        """Parse streaming chat response."""
        url = urljoin(self.host, "/v1/chat/completions")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")

        calls: Dict[int, Dict] = {}  # tool-call deltas arrive in fragments keyed by index
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
                            delta = dict(chunk["choices"][0]["delta"])
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue  # Skip malformed lines
                        for tc in delta.pop("tool_calls", None) or []:
                            acc = calls.setdefault(tc.get("index", 0), {
                                "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            acc["id"] = tc.get("id") or acc["id"]
                            fn = tc.get("function") or {}
                            acc["function"]["name"] += fn.get("name") or ""
                            acc["function"]["arguments"] += fn.get("arguments") or ""
                        if delta.get("content") or delta.get("role"):
                            yield {"message": delta}
            if calls:
                msg = {"role": "assistant", "content": "",
                       "tool_calls": [calls[i] for i in sorted(calls)]}
                for c in msg["tool_calls"]:
                    c["id"] = c["id"] or f"call_{uuid.uuid4().hex[:12]}"
                yield {"message": _from_openai_message(msg)}
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

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Batch embeddings: one HTTP call for the whole list; results re-ordered by `index`."""
        if not texts:
            return []
        response = self._make_request(
            "POST", "/v1/embeddings", {"model": self.embed_model, "input": list(texts)}
        )
        rows = sorted(response["data"], key=lambda r: r.get("index", 0))
        if len(rows) != len(texts):
            raise OllamaClientError(f"embedding count mismatch: {len(rows)} for {len(texts)} inputs")
        return [r["embedding"] for r in rows]

    def list_models(self) -> List[str]:
        """List available models on oMLX server."""
        response = self._make_request("GET", "/v1/models")
        return [model["id"] for model in response["data"]]

    def model_details(self) -> Dict[str, Any]:
        """Get detailed model information."""
        return {}

    def model_capabilities(self) -> Dict[str, Any]:
        """oMLX reports no capabilities; tool support is declared via config."""
        if not self._supports_tools:
            return {}
        try:
            return {name: ["tools"] for name in self.list_models()}
        except Exception:
            return {}

    def model_supports_tools(self, model_name: str = "") -> bool:
        return self._supports_tools

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