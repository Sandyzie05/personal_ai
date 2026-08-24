"""RAG (Retrieval-Augmented Generation) engine."""

import uuid
from typing import List, Dict, Any, Optional

from .embeddings import EmbeddingsGenerator
from ..ai_engine.chroma_store import (
    ChromaStore as _ChromaStore,
    ChromaStoreError as _ChromaStoreError,
)


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
    ):
        self.embedding_generator = embedding_generator
        self.chroma_store = chroma_store
        self.top_k = top_k
        self.persist_directory = persist_directory
        self._chroma_store_class = _ChromaStore
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Ensure the RAG engine is properly initialized."""
        if self.embedding_generator is None:
            self.embedding_generator = EmbeddingsGenerator()

    def _get_chroma_store(self) -> _ChromaStore:
        """Lazily initialize ChromaDB store."""
        if self.chroma_store is None:
            self.chroma_store = self._chroma_store_class(
                embedding_generator=self.embedding_generator,
                persist_directory=self.persist_directory,
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

    def retrieve_relevant(self, query: str, k: int = None) -> List[Dict[str, Any]]:
        """Retrieve documents relevant to a query."""
        if k is None:
            k = self.top_k

        if not self.embedding_generator:
            return []

        try:
            results = self._get_chroma_store().retrieve_relevant(query, k)
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

    def get_context_for_query(self, query: str) -> str:
        """Get context text for a query."""
        docs = self.retrieve_relevant(query, k=3)
        if docs:
            return "\n\n".join([doc["content"] for doc in docs])
        return ""
