"""Configuration page for Ollama settings."""

import streamlit as st
from typing import Dict, Any


def render_config_page() -> Dict[str, Any]:
    """Render configuration UI and return settings."""
    
    st.header("⚙️ Ollama Configuration")
    st.divider()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Ollama Settings")
        ollama_host = st.text_input(
            "Ollama Host",
            value="http://localhost:11434",
            help="URL of your Ollama instance"
        )
        
        ollama_model = st.text_input(
            "Chat Model",
            value="llama3.2:latest",
            help="Model to use for chat interactions"
        )
        
        ollama_embeddings = st.text_input(
            "Embeddings Model",
            value="nomic-embed-text",
            help="Model to use for vector embeddings"
        )
    
    with col2:
        st.subheader("Advanced Settings")
        temperature = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.7,
            step=0.1,
            help="Randomness of responses (higher = more creative)"
        )
        
        max_tokens = st.slider(
            "Max Tokens",
            min_value=100,
            max_value=8192,
            value=2048,
            step=100,
            help="Maximum length of generated responses"
        )
        
        stream = st.checkbox(
            "Stream responses",
            value=True,
            help="Show responses as they're generated (like typing)"
        )
    
    st.divider()
    
    if st.button("💾 Save Configuration"):
        settings = {
            "ollama_host": ollama_host,
            "ollama_model": ollama_model,
            "ollama_embeddings": ollama_embeddings,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }
        st.success("✅ Configuration saved successfully!")
        return settings
    
    st.caption("note: configuration is stored locally and never sent to external servers")
    
    return {
        "ollama_host": ollama_host,
        "ollama_model": ollama_model,
        "ollama_embeddings": ollama_embeddings,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream
    }
