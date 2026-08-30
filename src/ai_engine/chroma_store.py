"""ChromaDB vector store for RAG embeddings."""

import os
from typing import List, Dict, Any, Optional
import uuid

from .embeddings import EmbeddingsGenerator
from ..config import (
    DEFAULT_EMBED_CHUNK_TOKENS,
    DEFAULT_MIN_RELATIVE_SCORE,
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


def distance_to_similarity(cosine_distance: float) -> float:
    """Turn a ChromaDB cosine *distance* into a 0..1 *similarity*.

    ChromaDB's "cosine" space reports a distance of `1 - similarity`
    (range roughly [0, 2]), not a similarity. Retrieval thresholds work on
    "how similar", so everything downstream converts via this one function
    rather than each call site re-deriving the (easy to get backwards) sign.
    A perfect match is distance 0 -> similarity 1; unrelated vectors land near
    0 (or below, clamped).
    """
    return max(0.0, min(1.0, 1.0 - float(cosine_distance)))


def document_id(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """The source-document identity for a chunk, for dedup - or None.

    Chunks of one vault document all share its `storage_key`, so that's the
    natural "which document" identity. A chunk without a `storage_key` (e.g.
    one added straight via `RAGEngine.add_document` with no metadata) has no
    document identity, so it can't be grouped - return None and let the caller
    keep it rather than risk collapsing two unrelated chunks into one.
    """
    if not metadata:
        return None
    key = metadata.get("storage_key")
    return str(key) if key else None


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
        min_relevance: float = DEFAULT_MIN_RELATIVE_SCORE,
        max_per_document: int = 1,
    ):
        self.embedding_generator = embedding_generator
        self.collection_name = collection_name
        self.persist_directory = persist_directory or DEFAULT_CHROMA_DIR
        self.collection = None
        self._client = None
        self._initialized = False
        # Anti-hallucination knobs (see config.DEFAULT_MIN_RELATIVE_SCORE).
        # `min_relevance` < 0 disables the threshold entirely.
        self.min_relevance = min_relevance
        self.max_per_document = max(1, max_per_document)
        self._last_retrieval: List[Dict[str, Any]] = []

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
                    persist_directory=self.persist_directory,
                    anonymized_telemetry=False,
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
        self,
        query: str,
        k: int = 5,
        where: Optional[Dict[str, Any]] = None,
        min_relevance: Optional[float] = None,
        max_per_document: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant documents for a query, optionally scoped by metadata.

        `where` is a ChromaDB metadata filter (e.g. {"category": "electricity"})
        - used to scope retrieval to a document category instead of searching
        blindly across everything in the vault.

        Anti-hallucination filtering happens *here* so every retrieval path is
        consistent:

        - Over-fetch up to `k` candidates, then keep only chunks whose cosine
          SIMILARITY meets `min_relevance`. A near-miss returns [] rather than a
          confident-but-wrong answer. A threshold < 0 disables the cut.
        - Dedupe by source document (`document_id`), keeping the best
          `max_per_document` chunk(s), so one long bill can't crowd out the
          others.

        Results come back most-similar-first and are recorded on
        `last_retrieval()` for diagnostics/citations.
        """
        self._ensure_initialized()
        threshold = self.min_relevance if min_relevance is None else min_relevance
        per_doc = self.max_per_document if max_per_document is None else max_per_document
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

            docs: List[Dict[str, Any]] = []
            distances = results.get("distances", [None])[0] or []
            for i, doc in enumerate(results["documents"][0] or []):
                distance = distances[i] if i < len(distances) else 0.0
                docs.append(
                    {
                        "content": doc,
                        "metadata": (
                            results["metadatas"][0][i]
                            if results["metadatas"][0]
                            else {}
                        ),
                        "distance": distance,
                        # Similarity (0..1), not raw distance, so callers/tests
                        # read the "how relevant" intuition directly.
                        "score": distance_to_similarity(distance),
                    }
                )

            if threshold >= 0:
                docs = [d for d in docs if d["score"] >= threshold]
            docs = self._dedupe_by_document(docs, per_doc)
            docs.sort(key=lambda d: d["score"], reverse=True)

            self._last_retrieval = docs
            return docs

        except Exception as e:
            raise ChromaStoreError(f"Failed to retrieve documents: {str(e)}")

    @staticmethod
    def _dedupe_by_document(
        docs: List[Dict[str, Any]], max_per_document: int
    ) -> List[Dict[str, Any]]:
        """Keep at most `max_per_document` chunks per source document, by best
        similarity. Chunks with no document identity are always kept, since they
        can't be grouped into a document and must not be silently dropped."""
        if max_per_document <= 0:
            return docs
        kept: Dict[str, int] = {}
        deduped: List[Dict[str, Any]] = []
        for doc in docs:
            # Input arrives most-similar-first, so the first N for a doc are
            # its best chunks.
            doc_id = document_id(doc.get("metadata"))
            if doc_id is None:
                deduped.append(doc)
                continue
            if kept.get(doc_id, 0) < max_per_document:
                deduped.append(doc)
                kept[doc_id] = kept.get(doc_id, 0) + 1
        return deduped

    def last_retrieval(self) -> List[Dict[str, Any]]:
        """Most recent retrieval results - for diagnostics/observability only.

        Lets the chat layer log *which chunks and scores* were considered for an
        answer, so a hallucination can be traced back to weak or missing
        grounding instead of being invisible."""
        return list(self._last_retrieval)

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
    min_relevance: float = DEFAULT_MIN_RELATIVE_SCORE,
    max_per_document: int = 1,
) -> ChromaStore:
    """Factory function to create a ChromaDB store."""
    return ChromaStore(
        embedding_generator=embedding_generator,
        collection_name=collection_name,
        persist_directory=persist_directory,
        min_relevance=min_relevance,
        max_per_document=max_per_document,
    )
