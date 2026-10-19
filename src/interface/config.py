"""Configuration page for Ollama settings."""

import streamlit as st
from typing import Any, Dict, Optional

from src.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CHAT_TEMPERATURE,
    PERSONAL_AI_LLM_ENGINE,
    PERSONAL_AI_OMLX_HOST,
    PERSONAL_AI_OMLX_CHAT_MODEL,
    PERSONAL_AI_EMBED_ENGINE,
    PERSONAL_AI_OMLX_EMBED_MODEL,
)
from src.ai_engine.engine import build_chat_client, build_embed_client


def _get_available_models(engine: str, host: str) -> list:
    """Get list of available models for the selected engine and host."""
    try:
        if engine == "omlx":
            client = build_chat_client(engine="omlx", host=host)
        else:
            client = build_chat_client(engine="ollama", host=host)
        return client.list_models()
    except Exception:
        return []


def render_config_page(current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Render Ollama settings, prefilled from `current`, and return the values.

    Persistence is the caller's job (a single Save action in main.py), so
    there's exactly one Save button and one source of truth. `current` is
    the config loaded from the vault; missing keys fall back to defaults, so
    the form reflects what's actually saved instead of always showing
    defaults.
    """
    current = current or {}

    # Engine selection
    st.subheader("Engine")

    # Determine which engine to select based on current config or default
    engine = current.get("engine", PERSONAL_AI_LLM_ENGINE)
    embed_engine = current.get("embed_engine", PERSONAL_AI_EMBED_ENGINE)

    engine_radio = st.radio(
        "LLM engine",
        options=["ollama", "omlx"],
        index=0 if engine == "ollama" else 1,
        help="Choose which inference engine to use (Ollama or oMLX)",
    )

    embed_engine_radio = st.radio(
        "Embeddings engine",
        options=["ollama", "omlx"],
        index=0 if embed_engine == "ollama" else 1,
        help="Choose which inference engine to use for embeddings",
    )

    # Engine-specific settings
    with st.expander("LLM Settings", expanded=True):
        # Get the host based on the selected engine
        if engine_radio == "omlx":
            default_host = PERSONAL_AI_OMLX_HOST
        else:
            default_host = DEFAULT_OLLAMA_HOST

        llm_host = st.text_input(
            "LLM host",
            value=current.get("llm_host", default_host),
            help="URL of your LLM instance",
        )

        # Get available models based on engine and host
        available_models = _get_available_models(engine_radio, llm_host)

        if available_models:
            # Model selection with fallback to text input
            default_model = current.get("llm_model",
                                       PERSONAL_AI_OMLX_CHAT_MODEL if engine_radio == "omlx"
                                       else DEFAULT_CHAT_MODEL)
            selected_model = st.selectbox(
                "Chat model",
                options=available_models,
                index=available_models.index(default_model) if default_model in available_models else 0,
                help="Model used for chat interactions",
            )
        else:
            # Show text input if models can't be fetched
            default_model = current.get("llm_model",
                                       PERSONAL_AI_OMLX_CHAT_MODEL if engine_radio == "omlx"
                                       else DEFAULT_CHAT_MODEL)
            selected_model = st.text_input(
                "Chat model",
                value=default_model,
                help="Model used for chat interactions (no live model list)",
            )

    with st.expander("Embeddings Settings", expanded=True):
        # Get the embed host based on the selected embed engine
        if embed_engine_radio == "omlx":
            default_embed_host = PERSONAL_AI_OMLX_HOST
        else:
            default_embed_host = DEFAULT_OLLAMA_HOST

        embed_host = st.text_input(
            "Embeddings host",
            value=current.get("embed_host", default_embed_host),
            help="URL of your embedding instance",
        )

        # Get available embeddings models based on engine and host
        available_embed_models = _get_available_models(embed_engine_radio, embed_host)

        if available_embed_models:
            # Model selection with fallback to text input
            default_embed_model = current.get("embed_model",
                                              PERSONAL_AI_OMLX_EMBED_MODEL if embed_engine_radio == "omlx"
                                              else DEFAULT_EMBED_MODEL)
            selected_embed_model = st.selectbox(
                "Embeddings model",
                options=available_embed_models,
                index=available_embed_models.index(default_embed_model) if default_embed_model in available_embed_models else 0,
                help="Model used for vector embeddings",
            )
        else:
            # Show text input if models can't be fetched
            default_embed_model = current.get("embed_model",
                                              PERSONAL_AI_OMLX_EMBED_MODEL if embed_engine_radio == "omlx"
                                              else DEFAULT_EMBED_MODEL)
            selected_embed_model = st.text_input(
                "Embeddings model",
                value=default_embed_model,
                help="Model used for vector embeddings (no live model list)",
            )

    with st.expander("Generation Settings", expanded=True):
        st.subheader("Generation")
        temperature = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            value=float(current.get("temperature", DEFAULT_CHAT_TEMPERATURE)),
            step=0.1,
            help="Randomness of responses (higher = more creative). Lower "
            "values keep answers tightly grounded in your data.",
        )
        context_window = st.number_input(
            "Context window (tokens)",
            min_value=1024,
            max_value=128000,
            value=int(current.get("context_window_tokens", 8192)),
            step=1024,
            help="Context window size for the selected LLM (larger windows allow longer prompts)",
        )
        max_tokens = st.slider(
            "Max tokens",
            min_value=100,
            max_value=8192,
            value=int(current.get("max_tokens", 2048)),
            step=100,
            help="Maximum length of generated responses",
        )
        stream = st.checkbox(
            "Stream responses",
            value=bool(current.get("stream", True)),
            help="Show responses as they're generated (like typing)",
        )

    return {
        "engine": engine_radio,
        "llm_host": llm_host,
        "llm_model": selected_model,
        "embed_engine": embed_engine_radio,
        "embed_host": embed_host,
        "embed_model": selected_embed_model,
        "temperature": temperature,
        "context_window_tokens": context_window,
        "max_tokens": max_tokens,
        "stream": stream,
    }
