# AI Engine Module for Personal AI System
"""Local AI processing using Ollama and RAG."""

from .ollama_client import OllamaClient
from .embeddings import EmbeddingsGenerator
from .rag_engine import RAGEngine
from .model_manager import ModelManager

__all__ = ["OllamaClient", "EmbeddingsGenerator", "RAGEngine", "ModelManager"]
