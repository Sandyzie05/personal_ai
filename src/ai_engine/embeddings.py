"""Embedding engine for generating vector representations."""

from typing import List, Optional
import numpy as np

from .ollama_client import OllamaClient
from .engine import build_embed_client


class EmbeddingsError(Exception):
    """Custom exception for embedding engine errors."""
    pass


class EmbeddingsGenerator:
    """Handles embedding generation for RAG."""

    def __init__(self, client=None, model=None):
        # If no client provided, build one using the engine factory
        self.client = client or build_embed_client(model=model)
        self._query_prefix = ""
        self._document_prefix = ""
        self._embed_context_tokens = 2048  # Default fallback

        # Set prefixes and context tokens based on the embedding model
        if hasattr(self.client, 'model') and self.client.model:
            model_name = self.client.model
            if model_name.startswith("nomic-embed"):
                # For nomic models, keep the existing prefixes
                self._query_prefix = "search_query: "
                self._document_prefix = "search_document: "
                self._embed_context_tokens = 2048
            else:
                # For other models (e.g. Qwen), no prefixes
                self._query_prefix = ""
                self._document_prefix = ""
                # Get context tokens from config if available
                from ..config import EMBED_MODEL_CONTEXT_TOKENS
                self._embed_context_tokens = EMBED_MODEL_CONTEXT_TOKENS.get(model_name, 2048)

    @property
    def query_prefix(self) -> str:
        """Get query prefix for embedding."""
        return self._query_prefix

    @property
    def document_prefix(self) -> str:
        """Get document prefix for embedding."""
        return self._document_prefix

    @property
    def embed_context_tokens(self) -> int:
        """Get embedding context tokens."""
        return self._embed_context_tokens

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a text string."""
        if self.client:
            return self.client.generate_embedding(text)
        raise NotImplementedError("Embedding client required")

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return [self.generate_embedding(text) for text in texts]

    def similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Calculate cosine similarity between two embeddings."""
        arr1 = np.array(embedding1)
        arr2 = np.array(embedding2)

        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(arr1, arr2) / (norm1 * norm2))
