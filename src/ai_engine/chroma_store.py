"""ChromaDB vector store for RAG embeddings."""

import os
from typing import List, Dict, Any, Optional
import uuid

from .embeddings import EmbeddingsGenerator

# Default persist location if no vault-scoped path is supplied. Prefer
# passing persist_directory=<vault_path>/.chroma so the (unencrypted) search
# index lives next to the vault instead of the process's cwd.
DEFAULT_CHROMA_DIR = os.path.expanduser("~/.personal_ai_vault/.chroma")


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
        """Add documents to the vector store."""
        self._ensure_initialized()
        try:
            if not documents:
                return []

            if metadatas is None:
                metadatas = [{"_empty": True} for _ in documents]

            # Handle empty list case - convert to list of empty dicts
            if len(metadatas) == 0:
                metadatas = [{} for _ in documents]

            # Filter out empty dicts — ChromaDB rejects metadata with zero keys
            metadatas = [m if m else {"_empty": True} for m in metadatas]

            if self.embedding_generator:
                embeddings = [
                    self.embedding_generator.generate_embedding(doc)
                    for doc in documents
                ]
            else:
                embeddings = None

            ids = [f"doc_{uuid.uuid4().hex[:8]}" for _ in documents]

            self.collection.add(
                embeddings=embeddings, documents=documents, metadatas=metadatas, ids=ids
            )

            return ids

        except Exception as e:
            raise ChromaStoreError(f"Failed to add documents: {str(e)}")

    def retrieve_relevant(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant documents for a query."""
        self._ensure_initialized()
        try:
            if self.embedding_generator:
                query_embedding = self.embedding_generator.generate_embedding(query)
                results = self.collection.query(
                    query_embeddings=[query_embedding], n_results=k
                )
            else:
                results = self.collection.query(query_texts=[query], n_results=k)

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
