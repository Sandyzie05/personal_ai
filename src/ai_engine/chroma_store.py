"""ChromaDB vector store for RAG embeddings."""

import os
import re
import hashlib
import json
from typing import List, Dict, Any, Optional
import uuid

from .embeddings import EmbeddingsGenerator
from ..config import (
    DEFAULT_EMBED_CHUNK_TOKENS,
    DEFAULT_MIN_RELATIVE_SCORE,
    DEFAULT_MAX_PER_DOCUMENT,
    DEFAULT_CHUNK_OVERLAP_CHARS,
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


def _chunk_text(
    text: str, chunk_chars: int, overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS
) -> List[str]:
    """Split text into pieces no longer than `chunk_chars`.

    Splits on paragraph/line/whitespace boundaries when possible so chunks
    don't break mid-word, but never returns a piece longer than `chunk_chars`
    (a pathological run of non-whitespace is hard-split). Empty input yields
    an empty list.

    When a single paragraph/line is long enough to need word-splitting, the
    tail of each resulting chunk (up to `overlap_chars`) is carried into the
    start of the next, so a fact split right at the cut point still appears
    whole in at least one chunk.
    """
    text = (text or "").strip()
    if not text:
        return []

    cap = max(chunk_chars, 1)
    overlap = max(0, min(overlap_chars, cap - 1))

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
            if len(word) > cap:
                if current:
                    chunks.append(current)
                    current = ""
                while len(word) > cap:
                    chunks.append(word[:cap])
                    word = word[cap:]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= cap:
                current += " " + word
            else:
                chunks.append(current)
                tail_length = min(overlap, max(cap - len(word) - 1, 0))
                tail = current[-tail_length:] if tail_length else ""
                current = f"{tail} {word}".strip() if tail else word
        if current:
            chunks.append(current)

    return chunks


def _chunk_markdown(text: str, chunk_chars: int, overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS) -> List[str]:
    """Split markdown text into chunks respecting markdown structure.

    This function intelligently chunks markdown while preserving:
    - Headings (sections stay together)
    - Tables (whole tables stay together, split only at row boundaries)
    - Code blocks (atomic blocks)
    - Paragraphs (with appropriate handling)

    For efficiency, for large pieces that exceed the chunk size but don't contain
    markdown structure, falls back to regular text chunking.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Check if text contains markdown structure
    has_markdown = bool(re.search(r'^#{1,6}\s|^[\|\+]\s*[\w\-\s\|]*\|', text, re.MULTILINE))
    if not has_markdown:
        # If no markdown structure, fall back to regular chunking
        return _chunk_text(text, chunk_chars, overlap_chars)

    # Split text into markdown elements
    blocks = []
    current_block = ""

    # Split into blocks based on markdown patterns
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for headings
        if re.match(r'^#{1,6}\s', line):
            # Close previous block if it exists
            if current_block.strip():
                blocks.append(current_block.strip())
            current_block = line + '\n'
        # Check for table lines
        elif re.match(r'^\|.*\|$', line) or (line.strip() and (line.strip() == '|---|' or '|' in line.strip())):
            # Check if this is a table continuation or new table
            if current_block.strip() and not re.match(r'^#{1,6}\s', current_block.split('\n')[0]) and not current_block.strip().startswith('```'):
                # If previous block isn't a heading or code block, close it and start new block
                if current_block.strip():
                    blocks.append(current_block.strip())
                current_block = line + '\n'
            else:
                # Continue the current table block
                current_block += line + '\n'
        # Check for code blocks (fenced code blocks)
        elif line.startswith('```'):
            if current_block.strip() and not line.startswith('```'):
                # Close previous block if it exists
                if current_block.strip():
                    blocks.append(current_block.strip())
                current_block = line + '\n'
            elif current_block.strip():
                current_block += line + '\n'
                # Continue until we see matching backticks
                i += 1
                while i < len(lines):
                    line = lines[i]
                    current_block += line + '\n'
                    if line.startswith('```'):
                        break
                    i += 1
                i -= 1  # Adjust for increment in loop
            else:
                # If there's a code block at the start, add it
                current_block = line + '\n'
        else:
            # Regular paragraph
            if not current_block.strip():
                current_block = line + '\n'
            elif re.match(r'^#{1,6}\s', current_block.split('\n')[0]):
                # Close heading block and start new paragraph
                blocks.append(current_block.strip())
                current_block = line + '\n'
            else:
                current_block += line + '\n'
        i += 1

    # Add final block
    if current_block.strip():
        blocks.append(current_block.strip())

    # Now process blocks for chunking
    chunks = []
    current_chunk = ""
    overlap = max(0, min(overlap_chars, chunk_chars - 1))

    # Process each block
    for block in blocks:
        # Check if this block is a heading or table
        is_heading = re.match(r'^#{1,6}\s', block)
        is_table = bool(re.search(r'^\|.*\|$', block, re.MULTILINE))
        is_code = block.strip().startswith('```') and block.strip().endswith('```')

        # If block already exceeds chunk size, it will be split
        if len(block) <= chunk_chars:
            if not current_chunk:
                current_chunk = block
            elif is_heading and len(current_chunk) > 0:
                # Start new chunk when a heading comes in and previous chunk is not empty
                chunks.append(current_chunk)
                current_chunk = block
            else:
                # When not a heading, just append to current chunk
                # but avoid starting a new chunk for non-heading blocks with insufficient space
                # to maintain semantic cohesion
                if len(current_chunk + "\n" + block) <= chunk_chars:
                    current_chunk += "\n" + block
                else:
                    # If adding block would exceed chunk, close current chunk and start new
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = block
        else:
            # Block is too large to fit in a chunk - need to split it
            if is_table:
                # Split tables at row boundaries
                table_lines = block.split('\n')
                current_table_chunk = ""
                table_chunk = ""

                # Try to split the table while keeping rows together
                for line in table_lines:
                    if not line.strip():
                        continue
                    if len(current_table_chunk + line + '\n') <= chunk_chars:
                        current_table_chunk += line + '\n'
                    else:
                        # If we exceed, save previous chunk and start new one
                        if current_table_chunk.strip():
                            # Add prefix to table chunk (table head)
                            if table_chunk:
                                table_chunk = table_chunk.strip() + "\n" + current_table_chunk.strip()
                            else:
                                table_chunk = current_table_chunk.strip()
                            chunks.append(table_chunk)
                        current_table_chunk = line + '\n'
                        table_chunk = line + '\n'  # Keep track of table head

                if current_table_chunk.strip():
                    chunks.append(current_table_chunk.strip())
            else:
                # For regular large chunks (code or text), fall back to chunking with overlap
                if current_chunk:
                    chunks.append(current_chunk)
                # Use regular chunking for oversized blocks
                large_chunks = _chunk_text(block, chunk_chars, overlap)
                chunks.extend(large_chunks)
                current_chunk = ""
                continue

    # Add remaining chunk
    if current_chunk.strip():
        chunks.append(current_chunk)

    # Apply overlap between chunks
    if len(chunks) > 1:
        # Add overlap from last chunk to next chunk
        overlapping_chunks = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapping_chunks.append(chunk)
            else:
                prev_chunk = chunks[i-1]
                # Get overlap from previous chunk tail
                tail = prev_chunk[-overlap:] if overlap else ""
                if tail:
                    # If overlap exists, prepend it to current chunk
                    combined = tail + "\n" + chunk
                    if len(combined) <= chunk_chars:
                        overlapping_chunks.append(combined)
                    else:
                        overlapping_chunks.append(chunk)
                else:
                    overlapping_chunks.append(chunk)
        chunks = overlapping_chunks

    return [
        part
        for chunk in chunks
        for part in (
            _chunk_text(chunk, chunk_chars, overlap_chars)
            if len(chunk) > max(chunk_chars, 1)
            else [chunk]
        )
    ]


def chunk_text(text: str, embed_context_tokens: int = 2048) -> List[str]:
    """Intelligently chunk text, preferring markdown-aware chunks when possible."""
    # Calculate chunk size based on embedding model's context
    chunk_chars = min(
        DEFAULT_EMBED_CHUNK_TOKENS * CHARS_PER_TOKEN_ESTIMATE,
        HARD_CHUNK_CHAR_CAP,
        max(1, int(embed_context_tokens * CHARS_PER_TOKEN_ESTIMATE * 0.25)),
    )

    # Use markdown-aware chunking if the text seems to contain markdown
    if text and (re.search(r'^#{1,6}\s', text, re.MULTILINE) or
                 re.search(r'^\|.*\|$', text, re.MULTILINE) or
                 re.search(r'^```', text, re.MULTILINE)):
        return _chunk_markdown(text, chunk_chars)
    else:
        # Fall back to regular text chunking
        return _chunk_text(text, chunk_chars)


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
        collection_name: str = "personal_ai_vault_v3",
        persist_directory: Optional[str] = None,
        min_relevance: float = DEFAULT_MIN_RELATIVE_SCORE,
        max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
    ):
        self.embedding_generator = embedding_generator
        self.collection_name = collection_name
        client = getattr(embedding_generator, "client", None)
        model = getattr(client, "embed_model", None) or getattr(client, "model", None)
        if isinstance(model, str) and model:
            # Chroma retains collection dimensions even after every row is deleted.
            identity = (
                f"{type(client).__module__}.{type(client).__qualname__}",
                getattr(client, "host", ""),
                model,
            )
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
            self.collection_name = f"{collection_name}_{digest}"
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
                # Get prefixes from embedding generator
                query_prefix = getattr(self.embedding_generator, 'document_prefix', "")
                # Prefix only the text sent for embedding, not the stored/
                # returned `chunked_docs` - the LLM should see the raw chunk,
                # not the task instruction.
                embeddings = [
                    self.embedding_generator.generate_embedding(
                        query_prefix + doc
                    )
                    for doc in chunked_docs
                ]
            else:
                embeddings = None

            ids = [
                f"vault_{hashlib.sha256(source.encode()).hexdigest()}_{meta['chunk']}"
                if (source := document_id(meta)) else f"doc_{uuid.uuid4().hex}"
                for meta in chunked_metas
            ]

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
        per_doc = (
            self.max_per_document if max_per_document is None else max_per_document
        )
        try:
            query_kwargs: Dict[str, Any] = {"n_results": k}
            if where:
                query_kwargs["where"] = where

            if self.embedding_generator:
                # Get prefixes from embedding generator
                query_prefix = getattr(self.embedding_generator, 'query_prefix', "")
                query_embedding = self.embedding_generator.generate_embedding(
                    query_prefix + query
                )
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
            ids = self.collection.get(include=[])["ids"]
            if ids:
                self.collection.delete(ids=ids)
            self._last_retrieval = []
            return True
        except Exception as e:
            raise ChromaStoreError(f"Failed to delete documents: {str(e)}")

    def delete_document(self, storage_key: str) -> None:
        """Remove all chunks for one source, including legacy random chunk IDs."""
        self._ensure_initialized()
        try:
            self.collection.delete(where={"storage_key": storage_key})
            self._last_retrieval = []
        except Exception:
            raise ChromaStoreError("Failed to remove document chunks") from None

    def replace_document(self, content: str, metadata: Dict[str, Any]) -> List[str]:
        """Replace just this source; shrinking documents cannot retain old tails."""
        source = document_id(metadata)
        if not source:
            raise ChromaStoreError("Document replacement requires a storage key")
        self.delete_document(source)
        return self.add_documents([content], [metadata])

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
    collection_name: str = "personal_ai_vault_v2",
    persist_directory: Optional[str] = None,
    min_relevance: float = DEFAULT_MIN_RELATIVE_SCORE,
    max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
) -> ChromaStore:
    """Factory function to create a ChromaDB store."""
    return ChromaStore(
        embedding_generator=embedding_generator,
        collection_name=collection_name,
        persist_directory=persist_directory,
        min_relevance=min_relevance,
        max_per_document=max_per_document,
    )
