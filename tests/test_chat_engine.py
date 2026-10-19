"""Unit tests for src.ai_engine.chat_engine (prompt building, streaming)."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.ai_engine.chat_engine import (
    RESERVED_TOKENS,
    ChatEngine,
    ChatEngineError,
    estimate_tokens,
)
from src.ai_engine.ollama_client import OllamaClientError
from src.ai_engine.chroma_store import ChromaStore
from src.config import DEFAULT_CONTEXT_WINDOW_TOKENS


class FakeOllamaClient:
    """Ollama client double - no network/Ollama process involved."""

    def __init__(
        self,
        model_exists=True,
        response_text="hello",
        context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
    ):
        self._model_exists = model_exists
        self.response_text = response_text
        self.stream_chunks = []
        self.raise_on_chat = None
        self.last_messages = None
        self.context_window_tokens = context_window_tokens

    def model_exists(self, model_name):
        return self._model_exists

    def chat(self, messages, stream=False, format=None):
        self.last_messages = messages
        if self.raise_on_chat:
            raise self.raise_on_chat
        if stream:
            chunks = self.stream_chunks

            def _gen():
                for c in chunks:
                    yield {"message": {"content": c}}

            return _gen()
        return {"message": {"content": self.response_text}}


class FakeRagEngine:
    def __init__(self, context=""):
        self.context = context
        self.last_where = None

    def get_context_for_query(self, query, where=None):
        self.last_where = where
        return self.context


@pytest.mark.parametrize("configured", [False, True])
def test_configured_embeddings_preserve_lazy_rag_initialization(configured):
    embeddings = object() if configured else None
    with patch("src.ai_engine.chat_engine.DataVault") as vault_class, \
            patch("src.ai_engine.chat_engine.RAGEngine") as rag_class, \
            patch("src.ai_engine.chat_engine.EmbeddingsGenerator") as generator_class:
        vault = vault_class.return_value
        vault.vault_path = Path("synthetic-vault")
        vault.list_keys.return_value = ["synthetic-document"]
        vault.retrieve_data.side_effect = lambda key: (
            None if key == "ollama_config" else {"text_content": "synthetic public text"}
        )
        engine = ChatEngine(
            ollama_client=FakeOllamaClient(),
            vault_path="synthetic-vault",
            encryption_key=b"synthetic-key",
            embedding_generator=embeddings,
        )
        rag_class.assert_not_called()
        engine.initialize_rag()
        engine.initialize_rag()
        rag_class.assert_called_once_with(
            embedding_generator=embeddings if configured else generator_class.return_value,
            persist_directory=str(Path("synthetic-vault") / ".chroma"),
        )
        assert rag_class.return_value.clear.call_count == 2
        assert rag_class.return_value.add_documents.call_count == 2
        texts, metadata = rag_class.return_value.add_documents.call_args.args
        assert "synthetic public text" in texts[0]
        assert metadata[0]["storage_key"] == "synthetic-document"


def make_engine(tmp_path, **client_kwargs) -> ChatEngine:
    return ChatEngine(
        ollama_client=FakeOllamaClient(**client_kwargs),
        vault_path=str(tmp_path / "vault"),
        encryption_key=os.urandom(32),
    )


@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path)

@pytest.fixture
def indexed_engine(engine, monkeypatch):
    """Real chunking/RAG lifecycle backed by a synthetic in-memory collection."""
    rows = {}
    collection = MagicMock()

    def get(include=None):
        return {
            "ids": list(rows),
            "documents": [row[0] for row in rows.values()],
            "metadatas": [row[1] for row in rows.values()],
        }

    def add(ids, documents, metadatas, embeddings):
        assert not set(ids).intersection(rows)
        rows.update(zip(ids, zip(documents, metadatas)))

    def delete(ids=None, where=None):
        if where:
            ids = [
                key for key, row in rows.items()
                if all(row[1].get(k) == v for k, v in where.items())
            ]
        for chunk_id in ids:
            rows.pop(chunk_id)

    def query(n_results, where=None, **kwargs):
        matched = [
            row for row in rows.values()
            if not where or all(row[1].get(k) == v for k, v in where.items())
        ][:n_results]
        return {
            "documents": [[row[0] for row in matched]],
            "metadatas": [[row[1] for row in matched]],
            "distances": [[0.0] * len(matched)],
        }

    collection.get.side_effect = get
    collection.add.side_effect = add
    collection.delete.side_effect = delete
    collection.query.side_effect = query

    def initialize(store):
        store.collection = collection
        store._initialized = True

    monkeypatch.setattr(ChromaStore, "_ensure_initialized", initialize)
    engine._embedding_generator = MagicMock(
        document_prefix="", query_prefix="",
    )
    engine._embedding_generator.generate_embedding.return_value = [1.0, 0.0]
    return engine, collection, rows


def test_rebuild_twice_and_reopen_removes_legacy_duplicates(indexed_engine):
    engine, collection, rows = indexed_engine
    rows["legacy-random-id"] = ("obsolete synthetic text", {"storage_key": "removed"})
    engine.vault.store_data("note", {"text_content": "synthetic current note"})
    engine.vault.store_data("chat_history", {"messages": ["never indexed"]})

    engine.initialize_rag()
    first = dict(rows)
    engine.initialize_rag()
    assert rows == first
    assert len(rows) == 1
    assert "obsolete" not in engine.rag_engine.get_context_for_query("note")
    assert "never indexed" not in engine.rag_engine.get_context_for_query("note")

    engine.rag_engine = None  # Simulate a new session opening the persisted index.
    engine.initialize_rag()
    assert rows == first
    assert collection.delete.call_count == 3


def test_upload_replace_reclassify_delete_and_empty_rebuild(indexed_engine):
    engine, _, rows = indexed_engine
    data = {"text_content": "old synthetic text " * 400, "metadata": {"category": "gas"}}
    engine.vault.store_data("note", data)
    engine.index_document("note", data)
    assert len(rows) > 1
    rows["legacy-random-id"] = ("legacy tail", {"storage_key": "note", "category": "gas"})
    untouched = ("unrelated source", {"storage_key": "other", "category": "other"})
    rows["untouched-id"] = untouched

    replacement = {"text_content": "current synthetic text", "metadata": {"category": "electricity"}}
    engine.vault.store_data("note", replacement)
    engine.index_document("note", replacement)
    assert len(rows) == 2
    assert rows["untouched-id"] == untouched
    assert engine.rag_engine.get_context_for_query("q", where={"category": "gas"}) == ""
    context = engine.rag_engine.get_context_for_query("q", where={"category": "electricity"})
    assert "current synthetic text" in context
    assert "old synthetic text" not in context

    engine.vault.delete_data("note")
    engine.remove_document("note")
    assert rows == {"untouched-id": untouched}
    engine.initialize_rag()
    assert rows == {}
    assert engine.rag_engine.get_context_for_query("q") == ""


def test_one_hundred_upload_callbacks_embed_exactly_one_hundred_chunks(indexed_engine):
    engine, collection, rows = indexed_engine
    for i in range(100):
        key = f"synthetic-upload-{i}"
        data = {"text_content": f"synthetic public document {i}"}
        engine.vault.store_data(key, data)
        engine.index_document(key, data)

    assert len(rows) == 100
    assert engine._embedding_generator.generate_embedding.call_count == 100
    assert collection.get.call_count == 1  # Only the first callback initializes the snapshot.
    collection.delete.assert_any_call(where={"storage_key": "synthetic-upload-99"})


@pytest.mark.parametrize("key", ["ollama_config", "chat_history", "mcp_oauth_synthetic"])
def test_incremental_updates_never_index_internal_vault_records(indexed_engine, key):
    engine, _, rows = indexed_engine
    engine.initialize_rag()

    engine.add_data_to_vault(key, {"text_content": "synthetic internal record"})
    engine.index_document(key, {"text_content": "synthetic internal record"})

    assert rows == {}
    engine._embedding_generator.generate_embedding.assert_not_called()


def test_generic_agent_context_uses_saved_embedding_client_not_environment(engine):
    from src.agents.runtime import _default_context_engine_factory

    engine.vault.store_data("ollama_config", {
        "embed_engine": "ollama",
        "embed_host": "http://synthetic.invalid:1234",
        "embed_model": "synthetic-saved-embedding",
    })
    configured_client = SimpleNamespace(model="synthetic-saved-embedding")
    with patch("src.ai_engine.chat_engine.build_chat_client", return_value=FakeOllamaClient()), \
            patch("src.ai_engine.chat_engine.build_embed_client", return_value=configured_client) as build, \
            patch("src.ai_engine.chat_engine.RAGEngine") as rag:
        context = _default_context_engine_factory(engine.vault)({})
        build.assert_not_called()
        context.initialize_rag()

    build.assert_called_once_with(
        engine="ollama", host="http://synthetic.invalid:1234",
        model="synthetic-saved-embedding",
    )
    assert context._embedding_generator.client is configured_client
    assert rag.call_args.kwargs["embedding_generator"].client is configured_client


@pytest.mark.parametrize("identity_change", ["model", "host", "backend"])
def test_embedding_dimension_switch_uses_independent_collections(
    engine, monkeypatch, identity_change,
):
    from chromadb.api.segment import SegmentAPI

    collections = {}
    validator = SimpleNamespace(_sysdb=MagicMock())

    def initialize(store):
        if store.collection_name not in collections:
            rows = {}
            model = type("DimensionModel", (dict,), {"id": store.collection_name})(dimension=None)
            collection = MagicMock()

            def get(include=None):
                return {
                    "ids": list(rows),
                    "documents": [row[0] for row in rows.values()],
                    "metadatas": [row[1] for row in rows.values()],
                }

            def add(ids, documents, metadatas, embeddings):
                # Use installed Chroma validation: clearing rows cannot reset dimension.
                for vector in embeddings:
                    SegmentAPI._validate_dimension(validator, model, len(vector), update=True)
                rows.update(zip(ids, zip(documents, metadatas)))

            def delete(ids=None, where=None):
                for chunk_id in ids or []:
                    rows.pop(chunk_id)

            collection.get.side_effect = get
            collection.add.side_effect = add
            collection.delete.side_effect = delete
            collections[store.collection_name] = (collection, model, rows)
        store.collection = collections[store.collection_name][0]
        store._initialized = True

    class FirstBackend(SimpleNamespace):
        pass

    class SecondBackend(SimpleNamespace):
        pass

    def embeddings(dimension):
        backend = SecondBackend if identity_change == "backend" and dimension == 3 else FirstBackend
        return SimpleNamespace(
            client=backend(
                host=f"http://synthetic.invalid:{dimension}" if identity_change == "host" else "http://synthetic.invalid",
                model=f"synthetic-{dimension}d" if identity_change == "model" else "synthetic-model",
            ),
            document_prefix="", query_prefix="",
            generate_embedding=MagicMock(return_value=[1.0] * dimension),
        )

    monkeypatch.setattr(ChromaStore, "_ensure_initialized", initialize)
    engine.vault.store_data("note", {"text_content": "synthetic dimension test"})
    engine._embedding_generator = embeddings(2)
    engine.initialize_rag()
    first_name = engine.rag_engine.chroma_store.collection_name
    first_rows = dict(collections[first_name][2])

    switched = ChatEngine(
        ollama_client=FakeOllamaClient(),
        vault_path=str(engine.vault.vault_path),
        encryption_key=engine._encryption_key,
        embedding_generator=embeddings(3),
    )
    switched.initialize_rag()
    switched.initialize_rag()
    second_name = switched.rag_engine.chroma_store.collection_name

    assert first_name != second_name
    assert collections[first_name][1]["dimension"] == 2
    assert collections[second_name][1]["dimension"] == 3
    assert collections[first_name][2] == first_rows
    assert len(collections[second_name][2]) == 1


@pytest.mark.parametrize("failure", ["clear", "embedding", "read"])
def test_failed_rebuild_drops_queryable_engine_and_cannot_report_success(
    indexed_engine, monkeypatch, failure,
):
    engine, collection, _ = indexed_engine
    engine.vault.store_data("note", {"text_content": "synthetic text"})
    engine.initialize_rag()
    if failure == "clear":
        collection.delete.side_effect = RuntimeError("synthetic clear failure")
    elif failure == "embedding":
        engine._embedding_generator.generate_embedding.side_effect = RuntimeError("synthetic embedding failure")
    else:
        monkeypatch.setattr(engine.vault, "retrieve_data", MagicMock(side_effect=RuntimeError("synthetic read failure")))

    with pytest.raises(ChatEngineError, match="Failed to rebuild"):
        engine.initialize_rag()
    assert engine.rag_engine is None
    with pytest.raises(ChatEngineError):
        engine._build_prompt("synthetic question", use_rag=True)


@pytest.mark.parametrize("fails", [False, True])
def test_rebuild_action_calls_real_initializer_and_reports_only_its_result(
    indexed_engine, monkeypatch, fails,
):
    from src.interface import main as interface

    engine, collection, rows = indexed_engine
    engine.vault.store_data("note", {"text_content": "synthetic text"})
    if fails:
        collection.add.side_effect = RuntimeError("synthetic indexing failure")
    ui = MagicMock()
    monkeypatch.setattr(interface, "st", ui)
    app = interface.PersonalAIInterface()
    app.vault = engine.vault
    app.chat_engine = engine
    app.encryption_key = b"synthetic-key"
    app._initialized = True

    app._rebuild_rag_index()

    if fails:
        ui.success.assert_not_called()
        ui.error.assert_called_once()
        assert engine.rag_engine is None
    else:
        ui.success.assert_called_once()
        ui.error.assert_not_called()
        assert len(rows) == 1


@pytest.mark.parametrize("action", ["recat_note", "delete_note"])
@pytest.mark.parametrize("fails", [False, True])
def test_file_mutations_refresh_shared_index_before_success(
    indexed_engine, monkeypatch, action, fails,
):
    from src.interface import main as interface

    engine, _, rows = indexed_engine
    data = {"text_content": "synthetic text", "metadata": {"category": "gas"}}
    engine.vault.store_data("note", data)
    engine.initialize_rag()
    observer = engine.rag_engine
    if fails:
        method = "index_document" if action == "recat_note" else "remove_document"
        monkeypatch.setattr(engine, method, MagicMock(side_effect=ChatEngineError("synthetic failure")))
    ui = MagicMock()
    ui.columns.side_effect = lambda widths: [
        MagicMock() for _ in range(widths if isinstance(widths, int) else len(widths))
    ]
    ui.selectbox.return_value = "electricity"
    ui.button.side_effect = lambda *args, **kwargs: kwargs.get("key") == action
    monkeypatch.setattr(interface, "st", ui)
    app = interface.PersonalAIInterface()
    app.vault = engine.vault
    app.encryption_key = b"synthetic-key"
    app._initialized = True
    app._get_chat_engine_instance = MagicMock(return_value=engine)

    app._render_file_card("note", data, data["metadata"])

    app._get_chat_engine_instance.assert_called_once()
    if fails:
        ui.success.assert_not_called()
        ui.rerun.assert_not_called()
        ui.error.assert_called_once()
    else:
        ui.error.assert_not_called()
        ui.rerun.assert_called_once()
        assert observer.get_context_for_query("q", where={"category": "gas"}) == ""
        if action == "recat_note":
            assert engine.vault.retrieve_data("note")["metadata"]["category"] == "electricity"
            assert "synthetic text" in observer.get_context_for_query(
                "q", where={"category": "electricity"},
            )
            assert len(rows) == 1
        else:
            assert "note" not in engine.vault.list_keys()
            assert observer.get_context_for_query("q") == ""
            assert rows == {}


def test_data_to_text_skips_binary_and_flattens_nested_values(engine):
    data = {
        "raw_content_b64": "should never appear in RAG text",
        "text_content": "hello world",
        "metadata": {"a": 1, "b": "two"},
        "tags": ["x", "y"],
        "nested": {"inner": "value"},
        "num": 5,
    }

    text = engine._data_to_text(data)

    assert "should never appear in RAG text" not in text
    assert "hello world" in text
    assert "a: 1" in text
    assert "b: two" in text
    assert "tags: x, y" in text
    assert "inner: value" in text
    assert "num: 5" in text


def test_fit_context_to_budget_unchanged_when_small(engine):
    context = "short context"

    assert engine._fit_context_to_budget(context, "q", budget_chars=1000) == context


def test_fit_context_to_budget_truncates_when_too_large(engine):
    huge = "x" * 1_000_000

    result = engine._fit_context_to_budget(huge, "q", budget_chars=100)

    assert len(result) < len(huge)
    assert result.endswith("[...context truncated to fit model context window...]")


def test_estimate_tokens_basic_correctness():
    assert estimate_tokens("") == 0
    assert estimate_tokens("x" * 4) == 1
    assert estimate_tokens("x" * 40) == 10
    assert estimate_tokens("x" * 41) == 10


def test_fit_history_to_budget_keeps_all_when_under_budget(engine):
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    fitted = engine._fit_history_to_budget(history, budget_chars=1000)

    assert fitted == history


def test_fit_history_to_budget_drops_oldest_first_when_over_budget(engine):
    history = [
        {"role": "user", "content": "oldest message here"},
        {"role": "assistant", "content": "middle message here"},
        {"role": "user", "content": "newest"},
    ]

    fitted = engine._fit_history_to_budget(history, budget_chars=len("newest"))

    assert fitted == [{"role": "user", "content": "newest"}]


def test_fit_history_to_budget_handles_empty_list(engine):
    assert engine._fit_history_to_budget([], budget_chars=1000) == []


def test_fit_history_to_budget_zero_budget_drops_everything(engine):
    history = [{"role": "user", "content": "hi"}]

    assert engine._fit_history_to_budget(history, budget_chars=0) == []


def test_build_prompt_without_rag_uses_raw_query(engine):
    built = engine._build_prompt("what time is it?", use_rag=False)

    assert built["context_used"] is None
    assert built["messages"][0]["role"] == "system"
    assert built["messages"][-1] == {
        "role": "user",
        "content": "what time is it?",
    }


def test_build_prompt_with_rag_includes_context(engine):
    engine.rag_engine = FakeRagEngine(context="my note content")

    built = engine._build_prompt("what's in my note?", use_rag=True)

    assert built["context_used"] == "my note content"
    assert "my note content" in built["messages"][-1]["content"]
    assert "what's in my note?" in built["messages"][-1]["content"]


def test_build_prompt_with_rag_no_context_found(engine):
    engine.rag_engine = FakeRagEngine(context="")

    built = engine._build_prompt("anything", use_rag=True)

    assert built["context_used"] == ""
    # The refusal path now uses stronger, grounding-first language so a model
    # can't "answer from its own knowledge" when nothing was retrieved.
    assert "No data was found in the user's vault" in built["messages"][-1]["content"]
    assert "Do NOT answer from your own knowledge" in built["messages"][-1]["content"]


def test_system_prompt_enforces_grounding_and_politeness():
    """The system prompt must forbid guessing and set a warm, concise tone -
    the two levers that most directly reduce hallucination. A regression that
    reverts to the old "answer based on the provided context" one-liner is what
    this guards against."""
    prompt = ChatEngine.SYSTEM_PROMPT.lower()

    # Forbids confabulation: must name the "don't guess / don't make it up" rule.
    assert "guess" in prompt or "make anything up" in prompt
    # Explicit refusal, not a silent confident answer.
    assert "couldn't find" in prompt
    # Warm + concise, per the user's ask.
    assert "polite" in prompt
    assert "concise" in prompt


def test_query_vault_raises_when_model_missing(tmp_path):
    engine = make_engine(tmp_path, model_exists=False)

    with pytest.raises(ChatEngineError):
        engine.query_vault("hi", use_rag=False)


def test_query_vault_returns_response_and_context(engine):
    engine.rag_engine = FakeRagEngine(context="ctx")
    engine.ollama_client.response_text = "the answer"

    result = engine.query_vault("q", use_rag=True)

    assert result == {"query": "q", "response": "the answer", "context_used": "ctx"}


def test_query_vault_wraps_ollama_errors(engine):
    engine.ollama_client.raise_on_chat = OllamaClientError("boom")

    with pytest.raises(ChatEngineError):
        engine.query_vault("q", use_rag=False)


def test_query_vault_stream_yields_chunks_and_exposes_sources(engine):
    engine.rag_engine = FakeRagEngine(context="my context")
    engine.ollama_client.stream_chunks = ["Hel", "lo", " world"]

    stream = engine.query_vault_stream("q", use_rag=True)

    assert stream.sources == ["my context"]
    assert "".join(stream) == "Hello world"


def test_query_vault_stream_no_context_gives_no_sources(engine):
    engine.rag_engine = FakeRagEngine(context="")
    engine.ollama_client.stream_chunks = ["hi"]

    stream = engine.query_vault_stream("q", use_rag=True)

    assert stream.sources == []


def test_query_vault_stream_raises_immediately_when_model_missing(tmp_path):
    engine = make_engine(tmp_path, model_exists=False)

    with pytest.raises(ChatEngineError):
        engine.query_vault_stream("hi", use_rag=False)


def test_query_vault_stream_raises_during_iteration_on_ollama_error(engine):
    engine.rag_engine = FakeRagEngine(context="")
    engine.ollama_client.raise_on_chat = OllamaClientError("stream broke")

    stream = engine.query_vault_stream("q", use_rag=False)

    with pytest.raises(ChatEngineError):
        list(stream)


def _store_electricity_bill(
    engine, key, provider, period_start, period_end, line_items
):
    engine.vault.store_data(
        key,
        {
            "metadata": {"category": "electricity", "provider": provider},
            "text_content": f"{provider} electricity bill",
            "extraction": {
                "provider": provider,
                "period_start": period_start,
                "period_end": period_end,
                "total": sum(item["amount"] for item in line_items),
                "line_items": line_items,
            },
        },
    )


def test_get_structured_records_filters_by_category(engine):
    _store_electricity_bill(
        engine,
        "bill1",
        "PG&E",
        "2026-05-01",
        "2026-05-31",
        [{"label": "Usage", "amount": 100.0}],
    )
    engine.vault.store_data("note1", {"metadata": {}, "text_content": "unrelated note"})

    records = engine.get_structured_records(category="electricity")

    assert len(records) == 1
    assert records[0]["metadata"]["provider"] == "PG&E"


def test_get_structured_records_filters_by_date_range(engine):
    _store_electricity_bill(
        engine,
        "bill_in",
        "PG&E",
        "2026-05-01",
        "2026-05-31",
        [{"label": "Usage", "amount": 100.0}],
    )
    _store_electricity_bill(
        engine,
        "bill_out",
        "PG&E",
        "2026-01-01",
        "2026-01-31",
        [{"label": "Usage", "amount": 90.0}],
    )

    records = engine.get_structured_records(
        category="electricity", start_date="2026-04-01", end_date="2026-06-01"
    )

    assert [r["storage_key"] for r in records] == ["bill_in"]


def test_get_structured_records_excludes_undated_records_when_range_given(engine):
    engine.vault.store_data(
        "undated",
        {
            "metadata": {"category": "electricity", "provider": "PG&E"},
            "extraction": {
                "total": 50.0,
                "line_items": [{"label": "Usage", "amount": 50.0}],
            },
        },
    )

    records = engine.get_structured_records(
        category="electricity", start_date="2026-01-01", end_date="2026-12-31"
    )

    assert records == []


def test_build_prompt_uses_structured_records_for_aggregation_query(engine):
    _store_electricity_bill(
        engine,
        "bill1",
        "PG&E",
        "2026-06-01",
        "2026-06-30",
        [
            {"label": "Usage", "amount": 129.52},
            {"label": "Solar credit", "amount": -42.10},
        ],
    )

    built = engine._build_prompt("how much solar credit did I get?", use_rag=True)

    assert "Solar credit: -42.1" in built["context_used"]
    assert built["sources"] == ["PG&E (2026-06-01 to 2026-06-30): total=87.42"]
    assert "how much solar credit did I get?" in built["messages"][-1]["content"]


def test_build_prompt_falls_back_to_category_scoped_rag_when_no_structured_records(
    engine,
):
    engine.rag_engine = FakeRagEngine(context="some electricity text")

    built = engine._build_prompt("how much solar credit did I get?", use_rag=True)

    assert engine.rag_engine.last_where == {"category": "electricity"}
    assert built["context_used"] == "some electricity text"


def test_build_prompt_no_category_uses_unscoped_rag(engine):
    engine.rag_engine = FakeRagEngine(context="general context")

    built = engine._build_prompt("what's in my note?", use_rag=True)

    assert engine.rag_engine.last_where is None
    assert built["context_used"] == "general context"


def _store_bill(engine, key, category, provider, period_start, period_end, line_items):
    engine.vault.store_data(
        key,
        {
            "metadata": {"category": category, "provider": provider},
            "text_content": f"{provider} {category} statement",
            "extraction": {
                "provider": provider,
                "period_start": period_start,
                "period_end": period_end,
                "total": sum(item["amount"] for item in line_items),
                "line_items": line_items,
            },
        },
    )


def test_get_structured_records_filters_by_provider(engine):
    _store_bill(
        engine,
        "chase1",
        "credit_card",
        "Chase",
        "2026-06-01",
        "2026-06-30",
        [{"label": "Groceries", "amount": 100.0}],
    )
    _store_bill(
        engine,
        "capone1",
        "credit_card",
        "Capital One",
        "2026-06-01",
        "2026-06-30",
        [{"label": "Gas", "amount": 50.0}],
    )

    records = engine.get_structured_records(category="credit_card", provider="Chase")

    assert [r["storage_key"] for r in records] == ["chase1"]


def test_build_prompt_scopes_structured_records_to_named_provider(engine):
    """Two credit cards, same category - naming one in the question must not
    mix its numbers with the other's."""
    _store_bill(
        engine,
        "chase1",
        "credit_card",
        "Chase",
        "2026-06-01",
        "2026-06-30",
        [{"label": "Groceries", "amount": 100.0}],
    )
    _store_bill(
        engine,
        "capone1",
        "credit_card",
        "Capital One",
        "2026-06-01",
        "2026-06-30",
        [{"label": "Gas", "amount": 50.0}],
    )

    built = engine._build_prompt(
        "what did I spend on my Chase credit card?", use_rag=True
    )

    assert built["sources"] == ["Chase (2026-06-01 to 2026-06-30): total=100.00"]


def test_build_prompt_scopes_rag_by_provider_when_no_structured_records(engine):
    """Provider is known only from metadata (no extraction yet) - the
    RAG `where` filter should still pick it up, not just structured lookup."""
    engine.vault.store_data(
        "chase_no_extraction",
        {
            "metadata": {"category": "credit_card", "provider": "Chase"},
            "text_content": "Chase credit card statement",
        },
    )
    engine.rag_engine = FakeRagEngine(context="chase card text")

    built = engine._build_prompt(
        "what did I spend on my Chase credit card?", use_rag=True
    )

    assert engine.rag_engine.last_where == {
        "category": "credit_card",
        "provider": "Chase",
    }
    assert built["context_used"] == "chase card text"


def test_build_prompt_with_history_no_rag_orders_messages(engine):
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]

    built = engine._build_prompt("what time is it?", use_rag=False, history=history)

    assert built["messages"] == [
        {"role": "system", "content": engine.SYSTEM_PROMPT},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "what time is it?"},
    ]


def test_build_prompt_with_history_and_rag_orders_messages(engine):
    engine.rag_engine = FakeRagEngine(context="my note content")
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]

    built = engine._build_prompt("what's in my note?", use_rag=True, history=history)

    assert built["messages"][0] == {"role": "system", "content": engine.SYSTEM_PROMPT}
    assert built["messages"][1:3] == history
    assert built["messages"][-1]["role"] == "user"
    assert "what's in my note?" in built["messages"][-1]["content"]


def test_build_prompt_without_history_arg_matches_default_behavior(engine):
    engine.rag_engine = FakeRagEngine(context="my note content")

    built_no_history = engine._build_prompt("q", use_rag=True)
    built_empty_history = engine._build_prompt("q", use_rag=True, history=[])

    assert built_no_history["messages"] == built_empty_history["messages"]
    assert built_no_history["context_used"] == built_empty_history["context_used"]


def test_query_vault_passes_history_through_to_messages(engine):
    history = [{"role": "user", "content": "earlier"}]
    engine.rag_engine = FakeRagEngine(context="")

    engine.query_vault("q", use_rag=False, history=history)

    assert engine.ollama_client.last_messages == [
        {"role": "system", "content": engine.SYSTEM_PROMPT},
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "q"},
    ]


def test_fit_history_to_budget_used_when_history_too_large_for_window(tmp_path):
    engine = make_engine(tmp_path, context_window_tokens=600)
    engine.rag_engine = FakeRagEngine(context="")
    history = [
        {"role": "user", "content": "a" * 5000},
        {"role": "assistant", "content": "b" * 5000},
        {"role": "user", "content": "recent"},
    ]

    built = engine._build_prompt("q", use_rag=False, history=history)

    # Oldest messages should have been dropped to fit the small window.
    contents = [m["content"] for m in built["messages"]]
    assert "recent" in contents
    assert "a" * 5000 not in contents


def test_estimate_usage_more_history_increases_used_tokens_and_ratio(engine):
    baseline = engine.estimate_usage(history=[])
    with_history = engine.estimate_usage(
        history=[
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
    )

    assert with_history["used_tokens"] > baseline["used_tokens"]
    assert with_history["ratio"] > baseline["ratio"]
    assert (
        with_history["budget_tokens"]
        == engine.ollama_client.context_window_tokens - RESERVED_TOKENS
    )


def test_estimate_usage_reflects_ollama_client_context_window(tmp_path):
    engine = make_engine(tmp_path, context_window_tokens=4096)

    usage = engine.estimate_usage(history=[])

    assert usage["budget_tokens"] == 4096 - RESERVED_TOKENS
    assert usage["ratio"] == usage["used_tokens"] / (4096 - RESERVED_TOKENS)


def test_estimate_usage_empty_history_is_near_zero(engine):
    """A brand-new chat with no messages should show ~0%, not a fixed
    floor from the reply/headroom reserve leaking into "used"."""
    usage = engine.estimate_usage(history=[])

    assert usage["ratio"] < 0.02


def test_estimate_usage_accounts_for_pending_context(engine):
    without_context = engine.estimate_usage(history=[])
    with_context = engine.estimate_usage(history=[], pending_context="z" * 400)

    assert with_context["used_tokens"] > without_context["used_tokens"]
