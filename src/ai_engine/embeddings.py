"""Embedding engine for generating vector representations."""

from typing import List, Optional
import numpy as np

from .ollama_client import OllamaClient


class EmbeddingsError(Exception):
    """Custom exception for embedding engine errors."""
    pass


class EmbeddingsGenerator:
    """Handles embedding generation for RAG."""
    
    def __init__(self, ollama_client=None):
        self.ollama_client = ollama_client or OllamaClient()
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a text string."""
        if self.ollama_client:
            return self.ollama_client.generate_embedding(text)
        raise NotImplementedError("Ollama client required")
    
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
