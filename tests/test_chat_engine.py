"""Unit tests for src.ai_engine.chat_engine (prompt building, streaming)."""

import os

import pytest

from src.ai_engine.chat_engine import ChatEngine, ChatEngineError, estimate_tokens
from src.ai_engine.ollama_client import OllamaClientError
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
    assert with_history["budget_tokens"] == engine.ollama_client.context_window_tokens


def test_estimate_usage_reflects_ollama_client_context_window(tmp_path):
    engine = make_engine(tmp_path, context_window_tokens=4096)

    usage = engine.estimate_usage(history=[])

    assert usage["budget_tokens"] == 4096
    assert usage["ratio"] == usage["used_tokens"] / 4096


def test_estimate_usage_accounts_for_pending_context(engine):
    without_context = engine.estimate_usage(history=[])
    with_context = engine.estimate_usage(history=[], pending_context="z" * 400)

    assert with_context["used_tokens"] > without_context["used_tokens"]
