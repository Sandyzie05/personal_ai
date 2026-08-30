"""RAG (Retrieval-Augmented Generation) engine."""

import logging
import uuid
from typing import List, Dict, Any, Optional

from .embeddings import EmbeddingsGenerator
from ..config import DEFAULT_MIN_RELATIVE_SCORE, DEFAULT_MAX_PER_DOCUMENT
from ..ai_engine.chroma_store import (
    ChromaStore as _ChromaStore,
    ChromaStoreError as _ChromaStoreError,
)

logger = logging.getLogger(__name__)


class RAGEngineError(Exception):
    """Custom exception for RAG engine errors."""

    pass


class RAGEngine:
    """RAG system for local document retrieval and augmentation with ChromaDB."""

    def __init__(
        self,
        embedding_generator: Optional[EmbeddingsGenerator] = None,
        chroma_store: Optional[_ChromaStore] = None,
        top_k: int = 5,
        persist_directory: Optional[str] = None,
        min_relevance: float = DEFAULT_MIN_RELATIVE_SCORE,
        max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
    ):
        self.embedding_generator = embedding_generator
        self.chroma_store = chroma_store
        self.top_k = top_k
        self.persist_directory = persist_directory
        self.min_relevance = min_relevance
        self.max_per_document = max_per_document
        self._chroma_store_class = _ChromaStore
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Ensure the RAG engine is properly initialized."""
        if self.embedding_generator is None:
            self.embedding_generator = EmbeddingsGenerator()

    def _get_chroma_store(self) -> _ChromaStore:
        """Lazily initialize the ChromaDB store with the engine's relevance knobs."""
        if self.chroma_store is None:
            self.chroma_store = self._chroma_store_class(
                embedding_generator=self.embedding_generator,
                persist_directory=self.persist_directory,
                min_relevance=self.min_relevance,
                max_per_document=self.max_per_document,
            )
        return self.chroma_store

    def add_document(
        self, content: str, metadata: Dict[str, Any] = None, doc_id: str = None
    ) -> str:
        """Add a document to the knowledge base."""
        try:
            if doc_id is None:
                doc_id = f"doc_{uuid.uuid4().hex[:8]}"

            self._get_chroma_store().add_documents([content], [metadata or {}])
            return doc_id
        except _ChromaStoreError as e:
            raise RAGEngineError(f"Failed to add document: {str(e)}")

    def add_documents(
        self, documents: List[str], metadatas: List[Dict[str, Any]] = None
    ) -> List[str]:
        """Add multiple documents to the knowledge base."""
        try:
            if metadatas is None:
                metadatas = [{} for _ in documents]

            return self._get_chroma_store().add_documents(documents, metadatas)
        except _ChromaStoreError as e:
            raise RAGEngineError(f"Failed to add documents: {str(e)}")

    def retrieve_relevant(
        self, query: str, k: int = None, where: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve documents relevant to a query, optionally scoped by metadata."""
        if k is None:
            k = self.top_k

        if not self.embedding_generator:
            return []

        try:
            results = self._get_chroma_store().retrieve_relevant(query, k, where=where)
            self._log_retrieval(query, results)
            return results
        except _ChromaStoreError:
            return []

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents from the RAG index."""
        try:
            return self._get_chroma_store().get_all()
        except _ChromaStoreError:
            return []

    def clear(self) -> bool:
        """Clear all data from the RAG engine."""
        try:
            self._get_chroma_store().clear()
            return True
        except _ChromaStoreError:
            return False

    def get_context_for_query(
        self, query: str, where: Optional[Dict[str, Any]] = None, k: int = None
    ) -> str:
        """Get context text for a query, optionally scoped by metadata.

        Respects `self.top_k` (a hardcoded `k=3` previously ignored it) and
        labels each chunk with its source so the model can tell chunks apart and
        a hallucination can be traced back to a specific chunk + score.
        """
        if k is None:
            k = self.top_k
        docs = self.retrieve_relevant(query, k=k, where=where)
        if docs:
            labeled = [
                f"[Source {i}: {self._source_label(doc, i)}]\n{doc['content']}"
                for i, doc in enumerate(docs, 1)
            ]
            return "\n\n".join(labeled)
        return ""

    def _source_label(self, doc: Dict[str, Any], index: int) -> str:
        """Short human/citation label for a retrieved chunk."""
        meta = doc.get("metadata") or {}
        provider = (
            meta.get("provider")
            or meta.get("original_filename")
            or meta.get("category")
        )
        label = f"chunk #{index}"
        if provider:
            label = f"{label} · {provider}"
        return label

    def _log_retrieval(self, query: str, docs: List[Dict[str, Any]]) -> None:
        """Diagnostic log of what was (or wasn't) found, to debug hallucination.

        Surfaces whether a turn answered from grounding at all, and how strong
        the best chunk was - the single most useful signal for "why did the
        model make something up?".
        """
        if not docs:
            logger.info(
                "RAG retrieval for %r: no chunks met the relevance threshold "
                "(min %.2f) - answering ungrounded/not-found.",
                query,
                self.min_relevance,
            )
            return
        top = ", ".join(
            f"{self._source_label(doc, i)}=sim:{doc.get('score', 0.0):.2f}"
            for i, doc in enumerate(docs, 1)
        )
        logger.info("RAG retrieval for %r: %d chunk(s) [%s]", query, len(docs), top)
