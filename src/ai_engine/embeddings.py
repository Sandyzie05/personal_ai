"""Embedding engine for generating vector representations."""

import array
import base64
import hashlib
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
        # Optional get(key)->vector|None / put(key, vector): see VaultEmbeddingCache. Never on disk in plaintext.
        self.cache = None
        self.embed_calls = 0

        # Set prefixes and context tokens based on the embedding model
        if hasattr(self.client, 'model') and self.client.model:
            model_name = self.client.model
            if model_name.startswith("nomic-embed"):
                # For nomic models, keep the existing prefixes
                self._query_prefix = "search_query: "
                self._document_prefix = "search_document: "
                self._embed_context_tokens = 2048
            else:
                # Qwen3 embedding models are trained with an instruction on the query side only.
                from ..config import QWEN_QUERY_INSTRUCTION
                self._query_prefix = QWEN_QUERY_INSTRUCTION if "qwen" in model_name.lower() else ""
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

    def generate_embeddings(self, texts: List[str], salt: str = "") -> List[List[float]]:
        """Embed many texts: cached vectors are reused, the rest go out in batches (one call per batch).

        `salt` (e.g. the chunker version) is part of the cache key, so a format change never reuses old vectors."""
        model = getattr(self.client, "model", "") or ""
        keys = [hashlib.sha256(f"{model}\0{salt}\0{t}".encode()).hexdigest() for t in texts]
        out: List[Optional[List[float]]] = [self.cache.get(k) if self.cache else None for k in keys]
        todo = [i for i, v in enumerate(out) if v is None]
        from ..config import EMBED_BATCH_SIZE

        for start in range(0, len(todo), EMBED_BATCH_SIZE):
            idx = todo[start : start + EMBED_BATCH_SIZE]
            batch = [texts[i] for i in idx]
            if hasattr(self.client, "generate_embeddings"):
                vecs = self.client.generate_embeddings(batch)
            else:
                vecs = [self.client.generate_embedding(t) for t in batch]
            self.embed_calls += len(batch)
            for i, v in zip(idx, vecs):
                out[i] = v
                if self.cache:
                    self.cache.put(keys[i], v)
        return out  # type: ignore[return-value]

    def similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Calculate cosine similarity between two embeddings."""
        arr1 = np.array(embedding1)
        arr2 = np.array(embedding2)

        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(arr1, arr2) / (norm1 * norm2))


class VaultEmbeddingCache:
    """Embedding cache inside the encrypted vault (internal `vault_embcache_*` keys: never indexed or listed).

    Vectors are float32 base64 (about 11 KB for 2048 dims). Only the calling thread may use it (S15)."""

    PREFIX = "vault_embcache_"

    def __init__(self, vault):
        self.vault = vault

    def get(self, key: str) -> Optional[List[float]]:
        try:
            rec = self.vault.retrieve_data(self.PREFIX + key[:32])
        except Exception:
            return None
        if not rec or rec.get("k") != key:
            return None
        a = array.array("f")
        a.frombytes(base64.b64decode(rec["v"]))
        return a.tolist()

    def put(self, key: str, vec: List[float]) -> None:
        try:
            self.vault.store_data(
                self.PREFIX + key[:32], {"k": key, "v": base64.b64encode(array.array("f", vec).tobytes()).decode()}
            )
        except Exception:
            pass  # a cache write must never break indexing
