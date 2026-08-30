"""Configuration page for Ollama settings."""

import streamlit as st
from typing import Any, Dict, Optional

from src.config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CHAT_TEMPERATURE,
)


def render_config_page(current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Render Ollama settings, prefilled from `current`, and return the values.

    Persistence is the caller's job (a single Save action in main.py), so
    there's exactly one Save button and one source of truth. `current` is
    the config loaded from the vault; missing keys fall back to defaults, so
    the form reflects what's actually saved instead of always showing
    defaults.
    """
    current = current or {}

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Connection")
        ollama_host = st.text_input(
            "Ollama host",
            value=current.get("ollama_host", DEFAULT_OLLAMA_HOST),
            help="URL of your Ollama instance",
        )
        ollama_model = st.text_input(
            "Chat model",
            value=current.get("ollama_model", DEFAULT_CHAT_MODEL),
            help="Model used for chat interactions",
        )
        ollama_embeddings = st.text_input(
            "Embeddings model",
            value=current.get("ollama_embeddings", DEFAULT_EMBED_MODEL),
            help="Model used for vector embeddings",
        )

    with col2:
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
        "ollama_host": ollama_host,
        "ollama_model": ollama_model,
        "ollama_embeddings": ollama_embeddings,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
