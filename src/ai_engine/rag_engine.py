"""RAG (Retrieval-Augmented Generation) engine."""

import hashlib
import logging
import uuid
from typing import List, Dict, Any, Optional

from .embeddings import EmbeddingsGenerator
from ..config import DEFAULT_MIN_RELATIVE_SCORE, DEFAULT_MAX_PER_DOCUMENT
from ..ai_engine.chroma_store import (
    ChromaStore as _ChromaStore,
    contextual_header,
    ChromaStoreError as _ChromaStoreError,
)

logger = logging.getLogger(__name__)


def evidence_key(doc: Dict[str, Any]) -> str:
    meta = doc.get("metadata") or {}
    return str(meta.get("storage_key") or hashlib.sha256(doc["content"].encode()).hexdigest())


def evidence_id(key: str) -> str:
    """Stable citation handle for a source document: ev_ + 8 hex of sha256(storage key)."""
    return "ev_" + hashlib.sha256(key.encode()).hexdigest()[:8]


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _period(meta: Dict[str, Any]) -> str:
    start, end = meta.get("period_start"), meta.get("period_end")
    if start and end:
        return start if start[:7] == end[:7] else f"{start}..{end}"
    return start or end or ""


def _strip_header(content: str, meta: Dict[str, Any]) -> str:
    """The contextual header is embedded for retrieval; the block label already says it, so don't repeat it."""
    head = contextual_header(meta)
    return content[len(head) + 1 :] if head and content.startswith(head + "\n") else content


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

            store = self._get_chroma_store()
            if (metadata or {}).get("storage_key"):  # edited/re-uploaded doc: replace, don't pile up stale chunks
                store.delete_document(metadata["storage_key"])
            store.add_documents([content], [metadata or {}])
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
        """Context text for a query: one labelled block per source document.

        Block = `[Source n | provider | category | period | id=ev_xxxxxxxx]` + that document's retrieved chunks in
        page/chunk order. `n` is the relevance rank (1 = best) so a budget can drop the highest numbers first;
        blocks are *shown* newest period first. `id` is a stable evidence handle the answer must cite."""
        if k is None:
            k = self.top_k
        docs = self.retrieve_relevant(query, k=k, where=where)
        if not docs:
            return ""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for d in docs:  # docs arrive best-first
            groups.setdefault(evidence_key(d), []).append(d)
        blocks = []
        for rank, (key, chunks) in enumerate(groups.items(), 1):
            meta = chunks[0].get("metadata") or {}
            chunks.sort(key=lambda c: _int((c.get("metadata") or {}).get("chunk")))
            body = "\n".join(_strip_header(c["content"], c.get("metadata") or {}) for c in chunks)
            fields = [meta.get("provider") or meta.get("title"), meta.get("category"), _period(meta)]
            label = " | ".join(str(f) for f in [f"Source {rank}", *fields] if f) + f" | id={evidence_id(key)}"
            blocks.append((meta.get("period_end") or "", rank, f"[{label}]\n{body}"))
        blocks.sort(key=lambda b: (b[0] == "", [-ord(c) for c in b[0]], b[1]))
        return "\n\n".join(b[2] for b in blocks)

    def _source_label(self, doc: Dict[str, Any], index: int) -> str:
        """Short label for a retrieved chunk in diagnostics."""
        meta = doc.get("metadata") or {}
        provider = meta.get("provider") or meta.get("original_filename") or meta.get("category")
        return f"chunk #{index} · {provider}" if provider else f"chunk #{index}"

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
