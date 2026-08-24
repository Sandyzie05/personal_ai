"""Unit tests for src.ai_engine.chat_engine (prompt building, streaming)."""

import os

import pytest

from src.ai_engine.chat_engine import ChatEngine, ChatEngineError
from src.ai_engine.ollama_client import OllamaClientError


class FakeOllamaClient:
    """Ollama client double - no network/Ollama process involved."""

    def __init__(self, model_exists=True, response_text="hello"):
        self._model_exists = model_exists
        self.response_text = response_text
        self.stream_chunks = []
        self.raise_on_chat = None
        self.last_messages = None

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


def make_engine(tmp_path, **client_kwargs) -> ChatEngine:
    return ChatEngine(
        ollama_client=FakeOllamaClient(**client_kwargs),
        vault_path=str(tmp_path / "vault"),
        encryption_key=os.urandom(32),
    )


@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path)


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

    assert engine._fit_context_to_budget(context, "q") == context


def test_fit_context_to_budget_truncates_when_too_large(engine):
    huge = "x" * 1_000_000

    result = engine._fit_context_to_budget(huge, "q")

    assert len(result) < len(huge)
    assert result.endswith("[...context truncated to fit model context window...]")


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
    assert "No data found in vault" in built["messages"][-1]["content"]


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
