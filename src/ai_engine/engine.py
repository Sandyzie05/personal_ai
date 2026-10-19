"""Factory for creating LLM clients with different engines."""

from typing import Optional, Dict, Any

from .ollama_client import OllamaClient
from .omlx_client import OmlxClient
from ..config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    PERSONAL_AI_LLM_ENGINE,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_CHAT_MODEL,
    PERSONAL_AI_EMBED_ENGINE,
    PERSONAL_AI_OMLX_EMBED_MODEL,
    EMBED_MODEL_CONTEXT_TOKENS
)


def build_chat_client(engine: Optional[str] = None, host: Optional[str] = None,
                      model: Optional[str] = None, temperature: float = 0.7,
                      max_tokens: Optional[int] = None,
                      context_window_tokens: Optional[int] = None,
                      request_timeout_seconds: Optional[float] = None,
                      safe_local_transport: bool = False) -> Any:
    """Build chat client for the specified engine."""
    if engine is None:
        engine = PERSONAL_AI_LLM_ENGINE

    if host is None:
        if engine == "omlx":
            host = PERSONAL_AI_OMLX_HOST
        else:
            host = DEFAULT_OLLAMA_HOST

    if model is None:
        if engine == "omlx":
            model = PERSONAL_AI_OMLX_CHAT_MODEL
        else:
            model = DEFAULT_CHAT_MODEL

    if context_window_tokens is None:
        context_window_tokens = DEFAULT_CONTEXT_WINDOW_TOKENS
    transport_options = {}
    if request_timeout_seconds is not None:
        transport_options["request_timeout_seconds"] = request_timeout_seconds
    if safe_local_transport:
        transport_options["safe_local_transport"] = True

    if engine == "omlx":
        return OmlxClient(
            host=host,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window_tokens=context_window_tokens,
            **transport_options,
        )
    else:
        # Default to Ollama
        return OllamaClient(
            host=host,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window_tokens=context_window_tokens,
            **transport_options,
        )


def build_embed_client(engine: Optional[str] = None, host: Optional[str] = None,
                       model: Optional[str] = None) -> Any:
    """Build embedding client for the specified engine."""
    if engine is None:
        engine = PERSONAL_AI_EMBED_ENGINE

    if host is None:
        if engine == "omlx":
            host = PERSONAL_AI_OMLX_HOST
        else:
            host = DEFAULT_OLLAMA_HOST

    if model is None:
        if engine == "omlx":
            model = PERSONAL_AI_OMLX_EMBED_MODEL
        else:
            model = DEFAULT_EMBED_MODEL

    if engine == "omlx":
        return OmlxClient(
            host=host,
            model=model,
            temperature=0.0,  # Embeddings don't use temperature
            max_tokens=None,  # Embeddings don't use max_tokens
            context_window_tokens=EMBED_MODEL_CONTEXT_TOKENS.get(model, 2048)
        )
    else:
        # Default to Ollama for embeddings
        return OllamaClient(
            host=host,
            model=model,
            embed_model=model,
            temperature=0.0,  # Embeddings don't use temperature
            max_tokens=None,  # Embeddings don't use max_tokens
            context_window_tokens=EMBED_MODEL_CONTEXT_TOKENS.get(model, 2048)
        )


def client_kwargs_from_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Convert saved settings, retaining legacy Ollama keys and backend choices."""
    # Handle backward compatibility - if engine field is missing, assume Ollama
    engine = cfg.get("engine", "ollama")
    embed_engine = cfg.get("embed_engine", "ollama")

    # Handle missing keys in config
    return {
        "engine": engine,
        "host": cfg.get("llm_host") or (
            PERSONAL_AI_OMLX_HOST if engine == "omlx"
            else cfg.get("ollama_host") or DEFAULT_OLLAMA_HOST
        ),
        "model": cfg.get("llm_model") or (
            PERSONAL_AI_OMLX_CHAT_MODEL if engine == "omlx"
            else cfg.get("ollama_model") or DEFAULT_CHAT_MODEL
        ),
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens"),
        "context_window_tokens": cfg.get("context_window_tokens"),
        "embed_engine": embed_engine,
        "embed_host": cfg.get("embed_host"),
        "embed_model": cfg.get("embed_model"),
    }