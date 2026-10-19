"""Factory for creating LLM clients with different engines."""

import os
from typing import Optional, Dict, Any

from .ollama_client import OllamaClient
from .omlx_client import OmlxClient
from ..config import (
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
                      context_window_tokens: Optional[int] = None) -> Any:
    """Build chat client for the specified engine."""
    if engine is None:
        engine = PERSONAL_AI_LLM_ENGINE

    if host is None:
        if engine == "omlx":
            host = PERSONAL_AI_OMLX_HOST
        else:
            # For Ollama, use default Ollama host
            host = "http://localhost:11434"

    if model is None:
        if engine == "omlx":
            model = PERSONAL_AI_OMLX_CHAT_MODEL
        else:
            # For Ollama, we'll use default logic or parameter (can be set later)
            model = None

    if engine == "omlx":
        return OmlxClient(
            host=host,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window_tokens=context_window_tokens
        )
    else:
        # Default to Ollama
        return OllamaClient(
            host=host,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window_tokens=context_window_tokens
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
            # For Ollama, use default Ollama host
            host = "http://localhost:11434"

    if model is None:
        if engine == "omlx":
            model = PERSONAL_AI_OMLX_EMBED_MODEL
        else:
            # For Ollama, we'll use default logic or parameter
            model = None

    if engine == "omlx":
        # Create a simple client that handles embeddings for oMLX
        # The OmlxClient will be used for chat, and we need to handle embeddings properly
        # Return just a basic client with embedding context info
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
            temperature=0.0,  # Embeddings don't use temperature
            max_tokens=None,  # Embeddings don't use max_tokens
            context_window_tokens=EMBED_MODEL_CONTEXT_TOKENS.get(model, 2048)
        )


def client_kwargs_from_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Convert vault ollama_config dict to factory arguments."""
    # Handle backward compatibility - if engine field is missing, assume Ollama
    engine = cfg.get("engine", "ollama")
    embed_engine = cfg.get("embed_engine", "ollama")

    # Handle missing keys in config
    return {
        "engine": engine,
        "host": cfg.get("llm_host"),
        "model": cfg.get("llm_model"),
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens"),
        "context_window_tokens": cfg.get("context_window_tokens"),
        "embed_engine": embed_engine,
        "embed_host": cfg.get("embed_host"),
        "embed_model": cfg.get("embed_model"),
    }