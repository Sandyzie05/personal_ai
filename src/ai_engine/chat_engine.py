import hashlib
import re
from contextlib import contextmanager
from typing import Iterator, Optional, List, Dict, Any, Tuple

from .ollama_client import OllamaClient, OllamaClientError
from .engine import build_chat_client
from .query_analysis import (
    build_rag_filter,
    detect_category_from_query,
    detect_provider_from_query,
    iso_to_int,
    parse_relative_date_range,
)
from .embeddings import VaultEmbeddingCache
from .grounding import check as check_grounding, note as grounding_note
from .rag_engine import RAGEngine, evidence_id, evidence_key

from src.data_vault import DataVault, DataVaultError, is_internal_vault_key
from src.config import DEFAULT_CHAT_MODEL, CHARS_PER_TOKEN_ESTIMATE, RAG_QA_TEMPERATURE

# Reserve headroom for the system prompt, the user's question, and the
# model's own reply so RAG context alone can't push the request over the
# model's context window.
RESERVED_TOKENS = 1024

# Templated answer when retrieval finds nothing: the model is not asked to improvise one.
NOT_FOUND = "I cannot find this information in your vault."


def estimate_tokens(text: str) -> int:
    """Coarse chars-per-token estimate - good enough to budget against
    without adding a real tokenizer dependency. Shared by every place in
    this module (and the future UI context meter) that needs to turn text
    length into a token count."""
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


class ChatEngineError(Exception):
    """Custom exception for chat engine operations."""

    pass


class StreamingChatResponse:
    """Iterable wrapper around a token stream that also exposes `.sources`.

    RAG context is retrieved synchronously before the LLM call starts, so
    `.sources` is populated immediately - callers don't have to wait for the
    stream to finish (or reconstruct it from a generator's return value) to
    show citations alongside the streamed answer.
    """

    def __init__(self, chunks: Iterator[str], sources: List[str]):
        self.sources = sources
        self._chunks = chunks

    def __iter__(self) -> Iterator[str]:
        return iter(self._chunks)


class ChatEngine:
    """Chat engine that queries encrypted data vault using RAG and Llama models."""

    DEFAULT_MODEL = DEFAULT_CHAT_MODEL

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        vault_path: Optional[str] = None,
        encryption_key: Optional[bytes] = None,
        model: Optional[str] = None,
        context_window_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        rag_scope_categories: Optional[List[str]] = None,
    ):
        """Build the chat engine.

        The last four parameters are optional per-agent overrides for the
        multi-agent layer (docs/multi_agent_design.md D6). With all of them
        omitted, behavior is exactly the historical single-assistant one:
        default Ollama client, `ChatEngine.SYSTEM_PROMPT`, unscoped RAG.

        - `model` / `context_window_tokens`: generation settings. Applied to
          a provided `ollama_client` (in place) or used to build a new one.
        - `system_prompt`: replaces the grounding system prompt (the runtime
          passes agent instructions + skills assembled here).
        - `rag_scope_categories`: restricts both the structured-record scan
          and the ChromaDB `where` clause to these document categories
          (None/[] = unrestricted, today's behavior).
        """
        if ollama_client is None:
            client_kwargs: Dict[str, Any] = {}
            if model is not None:
                client_kwargs["model"] = model
            if context_window_tokens:
                client_kwargs["context_window_tokens"] = context_window_tokens
            # Use the engine factory to get the correct client
            self.ollama_client = build_chat_client(**client_kwargs)
        else:
            self.ollama_client = ollama_client
            if model is not None:
                self.ollama_client.model = model
            if context_window_tokens:
                self.ollama_client.context_window_tokens = context_window_tokens
        self.model = getattr(
            self.ollama_client, "model", None
        ) or self.DEFAULT_MODEL
        self.system_prompt = system_prompt or ChatEngine.SYSTEM_PROMPT
        self.rag_scope_categories: List[str] = (
            list(rag_scope_categories) if rag_scope_categories else []
        )

        self.vault = DataVault(vault_path=vault_path, encryption_key=encryption_key)
        self.rag_engine: Optional[RAGEngine] = None
        self._encryption_key = encryption_key
        self._model_loaded = False

    def _ensure_model(self, lazy: bool = False) -> None:
        """Ensure the chat model is available."""
        if lazy and self._model_loaded:
            return
        # Use the appropriate client for model existence checking
        if hasattr(self.ollama_client, 'model_exists'):
            if not self.ollama_client.model_exists(self.model):
                if lazy:
                    # Don't pull in lazy mode, just warn
                    self._model_loaded = True
                    return
                try:
                    self.ollama_client.pull_model(self.model)
                except OllamaClientError as e:
                    if not lazy:
                        # Determine if we're using oMLX or Ollama for error message
                        engine_name = "oMLX" if hasattr(self.ollama_client, 'host') and 'omlx' in self.ollama_client.host.lower() else "Ollama"
                        raise ChatEngineError(
                            f"Cannot chat without {self.model} model. "
                            f"Please load it manually on the {engine_name} server."
                        ) from e
        self._model_loaded = True

    def _load_vault_data_for_rag(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Load uploaded documents (not internal keys) as (text, metadata) pairs.

        Skips chat_history/ollama_config/vault_metadata - those aren't
        user documents and previously got embedded and indexed as if they
        were, wasting embedding calls and diluting retrieval quality.
        """
        items = []
        for key in self.vault.list_keys():
            if is_internal_vault_key(key):
                continue
            try:
                data = self.vault.retrieve_data(key)
                if data:
                    text = self._data_to_text(data)
                    items.append((text, self._rag_metadata_for(key, data)))
            except Exception:
                continue
        return items

    def _rag_metadata_for(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Metadata attached to a document's RAG chunks - enables category-
        and provider-scoped retrieval (see query_analysis.py + ChromaStore's
        `where`), and lets `RAGEngine._source_label` cite the actual
        institution instead of falling back to the filename."""
        metadata = data.get("metadata", {}) or {}
        rag_metadata: Dict[str, Any] = {"storage_key": key}
        for src, dst in (("category", "category"), ("provider", "provider"), ("account_label", "account_label"),
                         ("period_start", "period_start"), ("period_end", "period_end"),
                         ("original_filename", "title"), ("upload_timestamp", "upload_timestamp")):
            if metadata.get(src):
                rag_metadata[dst] = str(metadata[src])
        # Chroma range filters need numbers (see query_analysis.build_rag_filter).
        for k in ("period_start", "period_end"):
            if k in rag_metadata and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rag_metadata[k]):
                rag_metadata[f"{k}_n"] = iso_to_int(rag_metadata[k])
        # Unknown period = "could be any period" (never excluded by a period filter, ranked by relevance).
        rag_metadata.setdefault("period_start_n", 0)
        rag_metadata.setdefault("period_end_n", 99991231)
        text = data.get("text_content")
        if isinstance(text, str) and text:
            rag_metadata["content_hash"] = hashlib.sha256(text.encode()).hexdigest()[:16]
        return rag_metadata

    def _data_to_text(self, data: Dict[str, Any]) -> str:
        """Convert data dictionary to text for RAG."""
        text_parts = []
        for key, value in data.items():
            # Skip binary payloads (base64 of raw file bytes) - useless for
            # semantic retrieval and a token bomb for embedding models.
            if key == "raw_content_b64":
                continue
            if key == "text_content" and isinstance(value, str):
                text_parts.append(value)
            elif key == "metadata" and isinstance(value, dict):
                for meta_key, meta_value in value.items():
                    text_parts.append(f"{meta_key}: {meta_value}")
            elif isinstance(value, dict):
                text_parts.append(self._data_to_text(value))
            elif isinstance(value, list):
                text_parts.append(f"{key}: {', '.join(str(x) for x in value)}")
            else:
                text_parts.append(f"{key}: {value}")
        return " ".join(filter(None, text_parts))

    def _new_rag_engine(self) -> RAGEngine:
        # Colocate the search index with the vault; embeddings are cached inside the encrypted vault so an
        # unchanged document is never re-embedded (app open with no changes = 0 embed calls).
        engine = RAGEngine(persist_directory=str(self.vault.vault_path / ".chroma"))
        engine.embedding_generator.cache = VaultEmbeddingCache(self.vault)
        return engine

    def rag_index_stale(self) -> Optional[str]:
        """Reason the index no longer matches the embedding model/chunker (UI shows a rebuild prompt), or None."""
        if self.rag_engine is None or not hasattr(self.rag_engine, "_get_chroma_store"):
            return None
        try:
            return self.rag_engine._get_chroma_store().stale_reason()
        except Exception:
            return None

    def rebuild_rag_index(self) -> int:
        """Wipe the vector index and re-embed every vault document with the current
        embedding model. Returns the number of indexed documents."""
        if self.rag_engine is not None:
            self.rag_engine._get_chroma_store().reset()
        else:
            self.rag_engine = self._new_rag_engine()
            self.rag_engine._get_chroma_store().reset()
        self.rag_engine._initialized = True
        items = self._load_vault_data_for_rag()
        try:
            if items:
                self.rag_engine.add_documents(
                    [t for t, _ in items], [m for _, m in items]
                )
        except Exception as exc:  # the collection was already dropped: say so instead of leaving a silent empty index
            raise RuntimeError(
                "Index was cleared but re-embedding failed "
                f"({type(exc).__name__}); start the embedding server and run Rebuild RAG Index again."
            ) from exc
        return len(items)

    def initialize_rag(self) -> None:
        """Initialize RAG engine with vault data."""
        if self.rag_engine is None:
            # Colocate the (unencrypted) semantic search index with the vault
            # directory rather than the process cwd.
            self.rag_engine = self._new_rag_engine()
            self.rag_engine._initialized = True  # Skip lazy init checks
            items = self._load_vault_data_for_rag()
            if items:
                texts = [text for text, _ in items]
                metadatas = [metadata for _, metadata in items]
                self.rag_engine.add_documents(texts, metadatas)

    def get_structured_records(
        self,
        category: Optional[str] = None,
        provider: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Decrypt-and-filter uploaded documents by category/provider/date range.

        The vault has no native query engine (values are opaque encrypted
        blobs keyed by storage key), so this scans every key and filters in
        Python. That's an explicit, accepted tradeoff at personal-data scale
        (hundreds, not millions, of documents) rather than adding a second,
        separately-secured index.

        `provider` disambiguates multiple accounts in the same category
        (two credit cards, two checking accounts) - matched case-
        insensitively against the manually-entered `metadata.provider` or
        the LLM-extracted `extraction.provider`, whichever is present.

        When a date range is given, records missing period_start/period_end
        are excluded rather than guessed into range - better to under-match
        (and let the caller fall back to RAG/say "not found") than silently
        include a document that might be outside the requested window.
        """
        records = []
        for key in self.vault.list_keys():
            if is_internal_vault_key(key):
                continue
            try:
                data = self.vault.retrieve_data(key)
            except DataVaultError:
                continue
            if not data or not isinstance(data, dict):
                continue

            metadata = data.get("metadata", {}) or {}
            if self.rag_scope_categories and (
                metadata.get("category") not in self.rag_scope_categories
            ):
                continue
            if category and metadata.get("category") != category:
                continue

            extraction = data.get("extraction")
            if not extraction:
                continue

            if provider:
                record_provider = metadata.get("provider") or extraction.get("provider")
                if not record_provider or provider.lower() != record_provider.lower():
                    continue

            period_start = extraction.get("period_start")
            period_end = extraction.get("period_end")
            if (start_date or end_date) and not (period_start and period_end):
                continue
            if start_date and period_end and period_end < start_date:
                continue
            if end_date and period_start and period_start > end_date:
                continue

            records.append(
                {"storage_key": key, "metadata": metadata, "extraction": extraction}
            )

        return records

    def _known_providers_for_category(self, category: Optional[str]) -> List[str]:
        """Distinct `metadata.provider` values for vault documents in this
        category - independent of whether structured extraction succeeded.

        Used to resolve which of the user's own accounts a query names (see
        `query_analysis.detect_provider_from_query`). Deliberately doesn't
        reuse `get_structured_records` here: that method requires a
        completed extraction, but a document's provider is entered at
        upload time regardless of whether extraction later succeeds - a
        provider filter should still work for such a document.
        """
        providers = set()
        for key in self.vault.list_keys():
            if is_internal_vault_key(key):
                continue
            try:
                data = self.vault.retrieve_data(key)
            except DataVaultError:
                continue
            if not data or not isinstance(data, dict):
                continue
            metadata = data.get("metadata", {}) or {}
            if category and metadata.get("category") != category:
                continue
            provider = metadata.get("provider")
            if isinstance(provider, str) and provider.strip():
                providers.add(provider.strip())
        return sorted(providers)

    def _total_budget_chars(self, query: str) -> int:
        """Total character budget left for RAG/structured context + chat
        history combined, once the system prompt, the current query, and
        reply headroom are accounted for.

        Derived from `self.ollama_client.context_window_tokens` (not the
        module-level default) so this can never drift from what's actually
        sent to Ollama via `num_ctx`.
        """
        budget_tokens = max(
            self.ollama_client.context_window_tokens - RESERVED_TOKENS, 512
        )
        return budget_tokens * CHARS_PER_TOKEN_ESTIMATE - len(query)

    def _fit_context_to_budget(
        self, context: str, query: str, budget_chars: int
    ) -> str:
        """Truncate RAG/structured context so it fits within `budget_chars`.

        Uses a coarse chars-per-token estimate rather than a real tokenizer -
        good enough to prevent silent overflow without adding a heavy
        dependency. `make check-context` runs the same estimate offline.

        `budget_chars` is decided by the caller (see `_total_budget_chars`),
        which also has to leave room for chat history - this function no
        longer computes the whole-window budget itself.
        """
        if len(context) <= budget_chars:
            return context
        marker = "\n\n[...context truncated to fit model context window...]"
        blocks = re.split(r"\n\n(?=\[Source \d+)", context)
        if len(blocks) > 1 and all(b.startswith("[Source ") for b in blocks):
            # Drop whole blocks, least relevant (highest Source number) first: never cut a table in half.
            rank = lambda b: int(re.match(r"\[Source (\d+)", b).group(1))
            kept = sorted(blocks, key=rank)
            while len(kept) > 1 and len("\n\n".join(kept)) > budget_chars:
                kept.pop()
            blocks_kept = [b for b in blocks if b in kept]
            fitted = "\n\n".join(blocks_kept)
            if len(fitted) <= budget_chars:
                return fitted + marker
            context = fitted  # a single over-budget block: cut it at a line boundary below
        cut = context[:budget_chars]
        if "\n" in cut:
            cut = cut[: cut.rindex("\n")]
        return cut + marker

    def _fit_history_to_budget(
        self, history: List[Dict[str, str]], budget_chars: int
    ) -> List[Dict[str, str]]:
        """Return the longest suffix (most-recent-first fill) of `history`
        whose combined content length fits within `budget_chars`.

        Oldest messages are dropped first - the most recent turns are the
        ones most likely to matter for the current question. `history` is
        chronological (oldest-first); the result is returned in the same
        chronological order.
        """
        budget_chars = max(budget_chars, 0)
        fitted: List[Dict[str, str]] = []
        total = 0
        for message in reversed(history):
            length = len(message.get("content", ""))
            if total + length > budget_chars:
                break
            fitted.append(message)
            total += length
        fitted.reverse()
        return fitted

    SYSTEM_PROMPT = (
        "You are a warm, concise personal assistant that answers only from the "
        "context retrieved from the user's OWN local encrypted vault. You are "
        "polite and friendly, but accuracy comes first: never guess, estimate, "
        "or use your own knowledge to fill a gap in the context. If the "
        "context doesn't contain the answer, say so plainly (for example, "
        '"I couldn\'t find that in your vault") instead of making something up, '
        "and suggest what document the user could add. When you do answer, keep "
        "it short and cite the source you drew it from."
    )

    def _build_prompt(
        self,
        query: str,
        use_rag: bool = True,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build the chat messages, context, and sources for a query.

        Split out from query_vault/query_vault_stream so both the
        synchronous and streaming call paths - and unit tests - share one
        place that decides what gets sent to the model. Three paths, tried
        in order:

        1. Structured aggregation: the query unambiguously names a document
           category (see query_analysis.py) - pull the actual extracted
           numbers for matching documents and have the model compute from
           those, instead of summarizing fuzzy retrieved text. If the query
           also names one of the user's own known providers for that
           category (e.g. which of two credit cards), results are further
           scoped to that provider so two accounts never get mixed together.
        2. Category-scoped RAG: a category was named but no structured
           records matched (or none exist yet) - fall back to vector search
           scoped to that category (and provider, if detected) via
           ChromaDB's `where` filter.
        3. Plain RAG / no-RAG: no category detected, or use_rag=False.

        `history` (chronological, oldest-first, NOT including `query`
        itself) shares the same overall budget as RAG/structured context:
        context gets first claim on up to 70% of what's left after the
        system prompt/query/reply headroom, history gets whatever remains.
        """
        history = history or []

        if not use_rag:
            fitted_history = self._fit_history_to_budget(
                history, self._total_budget_chars(query)
            )
            return self._finalize_prompt(
                query, context=None, sources=[], history=fitted_history
            )

        category = detect_category_from_query(query)
        provider = None
        if category:
            known_providers = self._known_providers_for_category(category)
            provider = detect_provider_from_query(query, known_providers)
            date_range = parse_relative_date_range(query)
            start_date, end_date = date_range or (None, None)
            records = self.get_structured_records(
                category=category,
                provider=provider,
                start_date=start_date,
                end_date=end_date,
            )
            if records:
                return self._build_structured_prompt(query, records, history=history)

        if self.rag_engine is None:
            self.initialize_rag()

        # Query-named category/provider/period become metadata filters (a named out-of-scope category
        # falls back to the agent's whole scope; see build_rag_filter).
        date_range = parse_relative_date_range(query)
        if not category:
            provider = detect_provider_from_query(query, self._known_providers_for_category(None))
        where = build_rag_filter(category, provider, date_range, self.rag_scope_categories)
        context = self.rag_engine.get_context_for_query(query, where=where)

        total_budget_chars = self._total_budget_chars(query)
        context = self._fit_context_to_budget(
            context, query, budget_chars=int(total_budget_chars * 0.7)
        )
        history_budget_chars = total_budget_chars - len(context)
        fitted_history = self._fit_history_to_budget(history, history_budget_chars)

        sources = [context] if context else []
        return self._finalize_prompt(
            query, context=context, sources=sources, history=fitted_history
        )

    def _finalize_prompt(
        self,
        query: str,
        context: Optional[str],
        sources: List[str],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Shared tail of _build_prompt: turn context (or its absence) into messages.

        `history` here is assumed already budget-fitted by the caller - this
        just places it between the system message and the current user turn.
        """
        history = history or []

        if context is None:
            prompt = query
        elif context:
            prompt = (
                f"Context from your data (every source has an id):\n{context}\n\n"
                f"Question: {query}\n\n"
                "Answer using ONLY the context above. Quote each figure or date exactly as written there and "
                "cite the source id right after it, like [ev_0123abcd]. If sources disagree, show both and ask "
                f"which is right. If the answer is not in the context, say '{NOT_FOUND}'"
            )
        else:
            prompt = (
                "No data was found in the user's vault matching this query. "
                "Do NOT answer from your own knowledge or make anything up. "
                "Instead, kindly tell the user you couldn't find it in their "
                "vault and suggest what document they could upload to answer it.\n\n"
                f"Question: {query}"
            )

        messages = (
            [{"role": "system", "content": self.system_prompt}]
            + history
            + [{"role": "user", "content": prompt}]
        )
        return {"messages": messages, "context_used": context, "sources": sources}

    def _build_structured_prompt(
        self,
        query: str,
        records: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build a prompt that hands the model exact extracted numbers to compute from."""
        history = history or []
        summary = _format_structured_records(records)

        total_budget_chars = self._total_budget_chars(query)
        summary = self._fit_context_to_budget(
            summary, query, budget_chars=int(total_budget_chars * 0.7)
        )
        history_budget_chars = total_budget_chars - len(summary)
        fitted_history = self._fit_history_to_budget(history, history_budget_chars)

        prompt = (
            "The following is structured data extracted from the user's own "
            "uploaded documents that matches this question. Use ONLY these "
            "numbers to answer - do not estimate.\n\n"
            f"{summary}\n\n"
            f"Question: {query}\n\n"
            "Compute the answer from the data above, state the total, and "
            "briefly list which document(s)/period(s) it came from. If the "
            "data above doesn't fully answer the question, say what's "
            "missing rather than guessing."
        )
        messages = (
            [{"role": "system", "content": self.system_prompt}]
            + fitted_history
            + [{"role": "user", "content": prompt}]
        )
        sources = [_record_label(record) for record in records]
        return {"messages": messages, "context_used": summary, "sources": sources}

    def estimate_usage(
        self, history: List[Dict[str, str]], pending_context: str = ""
    ) -> Dict[str, Any]:
        """Estimate how much of the model's context window a chat turn would use.

        Pure computation over its inputs - no vault/RAG/Ollama calls - so a
        future UI layer can call this to show a "how full is the context
        window" meter without needing `initialize_rag()` or vault access.

        `budget_tokens` is the window minus RESERVED_TOKENS (the same
        reply/headroom reserve `_total_budget_chars` carves out), not the
        raw window size - RESERVED_TOKENS is headroom the conversation
        never gets to fill, not content that's already "used". Folding it
        into `used_tokens` instead used to put a ~1024-token floor under the
        meter, so a brand-new chat with zero messages showed ~13%
        "used" before the user had typed anything.
        """
        used_tokens = estimate_tokens(self.system_prompt)
        used_tokens += sum(
            estimate_tokens(message.get("content", "")) for message in history
        )
        used_tokens += estimate_tokens(pending_context)

        budget_tokens = max(
            self.ollama_client.context_window_tokens - RESERVED_TOKENS, 1
        )
        ratio = used_tokens / budget_tokens if budget_tokens else 0.0

        return {
            "used_tokens": used_tokens,
            "budget_tokens": budget_tokens,
            "ratio": ratio,
        }

    def query_vault(
        self,
        query: str,
        use_rag: bool = True,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Query the encrypted vault using chat model."""
        if not self.ollama_client.model_exists(self.model):
            raise ChatEngineError(f"Model '{self.model}' not available.")

        built = self._build_prompt(query, use_rag, history=history)
        context = built["context_used"]
        if use_rag and context == "":
            # Nothing relevant retrieved: templated not-found, the model is not asked to improvise.
            self._log_rag_turn(abstained=True)
            return {"query": query, "response": NOT_FOUND, "context_used": context}

        try:
            with self._qa_temperature(bool(context)):
                text = self.ollama_client.chat(messages=built["messages"]).get("message", {}).get("content", "")
                grounding = None
                if context and "[Source " in context:
                    verdict = check_grounding(text, context)
                    regenerated = not verdict.ok
                    if regenerated:  # one bounded retry with the offending claims named
                        retry = built["messages"] + [
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": (
                                "These parts of your answer are not supported by the context: "
                                f"{'; '.join(verdict.flagged()[:6])}. Answer again using only figures quoted from the "
                                f"context, each followed by its [ev_...] citation, or say '{NOT_FOUND}'")},
                        ]
                        text = self.ollama_client.chat(messages=retry).get("message", {}).get("content", "")
                        verdict = check_grounding(text, context)
                    if not verdict.ok:
                        text += grounding_note(verdict)
                    grounding = {"ok": verdict.ok, "flagged": verdict.flagged(), "regenerated": regenerated}
                    self._log_rag_turn(verdict=verdict, regenerated=regenerated)
            result = {"query": query, "response": text, "context_used": context}
            if grounding is not None:
                result["grounding"] = grounding
            return result
        except OllamaClientError as e:
            raise ChatEngineError(f"Chat generation failed: {str(e)}")

    @contextmanager
    def _qa_temperature(self, active: bool):
        """Document Q&A samples cold (RAG_QA_TEMPERATURE); restore the client's own setting afterwards."""
        client = self.ollama_client
        old = getattr(client, "temperature", None)
        if active and old is not None:
            client.temperature = RAG_QA_TEMPERATURE
        try:
            yield
        finally:
            if active and old is not None:
                client.temperature = old

    def _log_rag_turn(self, abstained: bool = False, verdict=None, regenerated: bool = False) -> None:
        """Per-turn retrieval diagnostics to the encrypted activity log: ids, scores, flags only (no text)."""
        try:
            from src.agents.store import log_activity

            store = getattr(self.rag_engine, "_get_chroma_store", None)
            chunks = store().last_retrieval() if store else []
            log_activity(self.vault, {
                "kind": "rag_turn", "abstained": abstained,
                "evidence_ids": sorted({evidence_id(evidence_key(c)) for c in chunks}),
                "scores": [round(c.get("score", 0.0), 3) for c in chunks],
                "grounded": None if verdict is None else verdict.ok,
                "flagged": 0 if verdict is None else len(verdict.flagged()), "regenerated": regenerated,
            })
        except Exception:
            pass  # diagnostics must never break a chat turn

    def query_vault_stream(
        self,
        query: str,
        use_rag: bool = True,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> StreamingChatResponse:
        """Stream a chat response chunk by chunk.

        RAG retrieval and the model-availability check happen eagerly
        (        before returning) so callers see a `ChatEngineError` immediately
        instead of partway through rendering a partial answer. `.sources` on
        the returned object is available right away; iterating it yields
        text deltas as they arrive from Ollama.
        """
        if not self.ollama_client.model_exists(self.model):
            raise ChatEngineError(f"Model '{self.model}' not available.")

        built = self._build_prompt(query, use_rag, history=history)
        sources = built["sources"]

        context = built["context_used"]
        if use_rag and context == "":
            self._log_rag_turn(abstained=True)
            return StreamingChatResponse(iter([NOT_FOUND]), sources)

        def _generate() -> Iterator[str]:
            parts: List[str] = []
            try:
                with self._qa_temperature(bool(context)):
                    for chunk in self.ollama_client.chat(
                        messages=built["messages"], stream=True
                    ):
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            parts.append(content)
                            yield content
            except OllamaClientError as e:
                raise ChatEngineError(f"Chat generation failed: {str(e)}") from e
            if context and "[Source " in context:
                # Already streamed, so no regenerate: append a visible note instead.
                verdict = check_grounding("".join(parts), context)
                self._log_rag_turn(verdict=verdict)
                if not verdict.ok:
                    yield grounding_note(verdict)

        return StreamingChatResponse(_generate(), sources)

    def add_data_to_vault(self, key: str, data: Dict[str, Any]) -> bool:
        """Add data to encrypted vault."""
        try:
            self.vault.store_data(key, data)

            if self.rag_engine:
                text = self._data_to_text(data)
                metadata = self._rag_metadata_for(key, data)
                self.rag_engine.add_document(text, metadata=metadata)

            return True
        except DataVaultError as e:
            raise ChatEngineError(f"Failed to store data: {str(e)}")

    def index_document(self, key: str, data: Dict[str, Any]) -> None:
        """Add an already-stored document to the RAG index without re-storing it.

        Used by the Upload page after FileUploadHandler writes a new document
        directly to the vault, so it's searchable immediately. initialize_rag()
        no-ops once already initialized, so a plain re-call after upload
        would silently skip indexing the new document - this handles both
        the not-yet-initialized case (defers to initialize_rag(), which will
        pick up this document along with everything else) and the
        already-initialized case (incremental add).
        """
        if self.rag_engine is None:
            self.initialize_rag()
            return
        text = self._data_to_text(data)
        metadata = self._rag_metadata_for(key, data)
        self.rag_engine.add_document(text, metadata=metadata)

    def clear_vault(self) -> bool:
        """Clear all data from vault and RAG index."""
        try:
            self.vault.clear()
            if self.rag_engine:
                self.rag_engine.clear()
            return True
        except Exception as e:
            raise ChatEngineError(f"Failed to clear vault: {str(e)}")


def _format_structured_records(records: List[Dict[str, Any]]) -> str:
    """Render structured records as plain text for the model to compute from."""
    lines = []
    for record in records:
        lines.append(f"- {_record_label(record)}")
        for item in record["extraction"].get("line_items", []):
            lines.append(f"    - {item.get('label')}: {item.get('amount')}")
    return "\n".join(lines)


def _record_label(record: Dict[str, Any]) -> str:
    """Short human-readable label for one structured record (provider +
    account + period + total) - the account piece is what lets the model
    tell two cards/accounts from the same provider apart in its answer."""
    metadata = record["metadata"]
    extraction = record["extraction"]
    provider = (
        metadata.get("provider") or extraction.get("provider") or "Unknown provider"
    )
    account = metadata.get("account_label") or extraction.get("account_identifier")
    provider_label = f"{provider} ({account})" if account else provider
    period_start = extraction.get("period_start", "?")
    period_end = extraction.get("period_end", "?")
    total = extraction.get("total")
    total_str = f"{total:.2f}" if isinstance(total, (int, float)) else str(total)
    return f"{provider_label} ({period_start} to {period_end}): total={total_str}"
