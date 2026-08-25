"""ChromaDB vector store for RAG embeddings."""

import os
from typing import List, Dict, Any, Optional
import uuid

from .embeddings import EmbeddingsGenerator
from ..config import (
    DEFAULT_EMBED_CHUNK_TOKENS,
    CHARS_PER_TOKEN_ESTIMATE,
)

# Default persist location if no vault-scoped path is supplied. Prefer
# passing persist_directory=<vault_path>/.chroma so the (unencrypted) search
# index lives next to the vault instead of the process's cwd.
DEFAULT_CHROMA_DIR = os.path.expanduser("~/.personal_ai_vault/.chroma")

# Even if DEFAULT_EMBED_CHUNK_TOKENS is misconfigured large, hard-cap any single
# chunk below a conservative embedding window so no chunk can overflow the
# embedding model and make Ollama return 500 on add_documents(). 2000 chars is
# safe even for base64 (which tokenizes ~1 char/token) against nomic-embed-text's
# real 2048-token context.
HARD_CHUNK_CHAR_CAP = 2000


def _chunk_text(text: str, chunk_chars: int) -> List[str]:
    """Split text into pieces no longer than `chunk_chars`.

    Splits on paragraph/line/whitespace boundaries when possible so chunks
    don't break mid-word, but never returns a piece longer than `chunk_chars`
    (a pathological run of non-whitespace is hard-split). Empty input yields
    an empty list.
    """
    text = (text or "").strip()
    if not text:
        return []

    cap = max(chunk_chars, 1)

    # Prefer paragraph, then line, then word boundaries.
    pieces = text.split("\n")
    pieces = [p for p in (p.strip() for p in pieces) if p]
    if not pieces:
        pieces = [text.strip()]

    chunks: List[str] = []
    for piece in pieces:
        if len(piece) <= cap:
            chunks.append(piece)
            continue
        words = piece.split()
        current = ""
        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= cap:
                current += " " + word
            else:
                chunks.append(current)
                current = word
        # A single word/pathological token can still exceed cap; hard-split it.
        if len(current) > cap:
            step = cap
            while len(current) > step:
                chunks.append(current[:step])
                current = current[step:]
        if current:
            chunks.append(current)

    return chunks


class ChromaStoreError(Exception):
    """Custom exception for ChromaDB operations."""

    pass


class ChromaStore:
    """ChromaDB vector store for document retrieval.

    Note: documents are stored here in plaintext (embeddings + raw text) to
    support local semantic search. This is a deliberate, documented residual
    risk - see docs/security_design.md - mitigated by keeping this directory
    colocated with the vault and locked down to 0700 permissions.
    """

    def __init__(
        self,
        embedding_generator: Optional[EmbeddingsGenerator] = None,
        collection_name: str = "personal_ai_vault",
        persist_directory: Optional[str] = None,
    ):
        self.embedding_generator = embedding_generator
        self.collection_name = collection_name
        self.persist_directory = persist_directory or DEFAULT_CHROMA_DIR
        self.collection = None
        self._client = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Initialize ChromaDB client and collection lazily."""
        if self._initialized:
            return
        try:
            import chromadb
            from chromadb.config import Settings

            os.makedirs(self.persist_directory, mode=0o700, exist_ok=True)
            os.chmod(self.persist_directory, 0o700)

            self._client = chromadb.Client(
                Settings(
                    persist_directory=self.persist_directory, anonymized_telemetry=False
                )
            )

            self.collection = self._client.get_or_create_collection(
                name=self.collection_name, metadata={"hnsw:space": "cosine"}
            )
            self._initialized = True
        except Exception as e:
            raise ChromaStoreError(f"Failed to initialize ChromaDB: {str(e)}")

    def add_documents(
        self, documents: List[str], metadatas: List[Dict[str, Any]] = None
    ) -> List[str]:
        """Add documents to the vector store.

        Each document is split into chunks small enough to fit any embedding
        model's context window before embedding. Without this, a single vault
        document larger than the embedding model's window makes Ollama return
        500 on the /embeddings call and aborts the entire index build (see
        src/config.DEFAULT_EMBED_CHUNK_TOKENS).
        """
        self._ensure_initialized()
        try:
            if not documents:
                return []

            chunk_chars = min(
                DEFAULT_EMBED_CHUNK_TOKENS * CHARS_PER_TOKEN_ESTIMATE,
                HARD_CHUNK_CHAR_CAP,
            )

            if not metadatas:
                metadatas = [{} for _ in documents]

            # Split every document into embeddable chunks, repeating its
            # metadata for each chunk so retrieval still points back to it.
            chunked_docs: List[str] = []
            chunked_metas: List[Dict[str, Any]] = []
            for doc, meta in zip(documents, metadatas):
                for i, chunk in enumerate(_chunk_text(doc, chunk_chars)):
                    chunked_docs.append(chunk)
                    # `chunk` key also guarantees ChromaDB never sees the
                    # zero-key metadata dict it rejects on add().
                    chunked_metas.append(dict(meta or {}, chunk=str(i + 1)))

            if not chunked_docs:
                return []

            if self.embedding_generator:
                embeddings = [
                    self.embedding_generator.generate_embedding(doc)
                    for doc in chunked_docs
                ]
            else:
                embeddings = None

            ids = [f"doc_{uuid.uuid4().hex[:8]}" for _ in chunked_docs]

            self.collection.add(
                embeddings=embeddings,
                documents=chunked_docs,
                metadatas=chunked_metas,
                ids=ids,
            )

            return ids

        except Exception as e:
            raise ChromaStoreError(f"Failed to add documents: {str(e)}")

    def retrieve_relevant(
        self, query: str, k: int = 5, where: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant documents for a query, optionally scoped by metadata.

        `where` is a ChromaDB metadata filter (e.g. {"category": "electricity"})
        - used to scope retrieval to a document category instead of searching
        blindly across everything in the vault.
        """
        self._ensure_initialized()
        try:
            query_kwargs: Dict[str, Any] = {"n_results": k}
            if where:
                query_kwargs["where"] = where

            if self.embedding_generator:
                query_embedding = self.embedding_generator.generate_embedding(query)
                results = self.collection.query(
                    query_embeddings=[query_embedding], **query_kwargs
                )
            else:
                results = self.collection.query(query_texts=[query], **query_kwargs)

            docs = []
            for i, doc in enumerate(results["documents"][0]):
                docs.append(
                    {
                        "content": doc,
                        "metadata": results["metadatas"][0][i]
                        if results["metadatas"][0]
                        else {},
                        "score": results["distances"][0][i]
                        if results["distances"][0]
                        else 0.0,
                    }
                )

            return docs

        except Exception as e:
            raise ChromaStoreError(f"Failed to retrieve documents: {str(e)}")

    def delete_all(self) -> bool:
        """Delete all documents from the collection."""
        self._ensure_initialized()
        try:
            self.collection.delete(where={"id": {"$ne": ""}})
            return True
        except Exception as e:
            raise ChromaStoreError(f"Failed to delete documents: {str(e)}")

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all documents from the collection."""
        self._ensure_initialized()
        try:
            results = self.collection.get()

            docs = []
            for i, doc in enumerate(results["documents"] or []):
                docs.append(
                    {
                        "content": doc,
                        "metadata": results["metadatas"][i]
                        if results["metadatas"]
                        else {},
                        "id": results["ids"][i] if results["ids"] else "",
                    }
                )

            return docs

        except Exception as e:
            raise ChromaStoreError(f"Failed to get documents: {str(e)}")

    def clear(self) -> bool:
        """Clear all data from the store."""
        self._ensure_initialized()
        try:
            self.delete_all()
            return True
        except Exception as e:
            raise ChromaStoreError(f"Failed to clear store: {str(e)}")


def create_chroma_store(
    embedding_generator: Optional[EmbeddingsGenerator] = None,
    collection_name: str = "personal_ai_vault",
    persist_directory: Optional[str] = None,
) -> ChromaStore:
    """Factory function to create a ChromaDB store."""
    return ChromaStore(
        embedding_generator=embedding_generator,
        collection_name=collection_name,
        persist_directory=persist_directory,
    )
