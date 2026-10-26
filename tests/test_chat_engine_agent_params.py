"""Unit tests for ChatEngine's per-agent constructor parameters
(model, context_window_tokens, system_prompt, rag_scope_categories).

Guards two things: the new params work, and the default construction stays
byte-identical to the historical single-assistant behavior (the existing
tests/test_chat_engine.py covers the latter end to end).
"""

import os

import pytest

from src.ai_engine.chat_engine import ChatEngine, RESERVED_TOKENS
from src.config import DEFAULT_CHAT_MODEL, DEFAULT_CONTEXT_WINDOW_TOKENS


class FakeOllamaClient:
    def __init__(self, context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS):
        self.context_window_tokens = context_window_tokens
        self.last_messages = None

    def model_exists(self, model_name):
        return True

    def chat(self, messages, stream=False, format=None, tools=None):
        self.last_messages = messages
        return {"message": {"content": "ok"}}


class FakeRagEngine:
    def __init__(self, context=""):
        self.context = context
        self.last_where = None

    def get_context_for_query(self, query, where=None):
        self.last_where = where
        return self.context


def make_engine(tmp_path, **kwargs) -> ChatEngine:
    params = dict(
        ollama_client=FakeOllamaClient(),
        vault_path=str(tmp_path / "vault"),
        encryption_key=os.urandom(32),
    )
    params.update(kwargs)
    return ChatEngine(**params)


def _store_bill(engine, key, category, provider=None, with_extraction=True):
    record = {
        "metadata": {"category": category, **({"provider": provider} if provider else {})},
        "text_content": f"{category} document",
    }
    if with_extraction:
        record["extraction"] = {
            "provider": provider,
            "period_start": "2026-05-01",
            "period_end": "2026-05-31",
            "total": 100.0,
            "line_items": [{"label": "Usage", "amount": 100.0}],
        }
    engine.vault.store_data(key, record)


class TestDefaultsUnchanged:
    def test_default_construction_matches_historical_behavior(self, tmp_path):
        engine = make_engine(tmp_path)

        assert engine.model == DEFAULT_CHAT_MODEL
        assert engine.system_prompt == ChatEngine.SYSTEM_PROMPT
        assert engine.rag_scope_categories == []
        assert engine.ollama_client.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS


class TestSystemPromptParam:
    def test_custom_prompt_replaces_default_in_messages(self, tmp_path):
        engine = make_engine(tmp_path, system_prompt="You are a trading copilot.")

        built = engine._build_prompt("hi", use_rag=False)

        assert built["messages"][0] == {
            "role": "system",
            "content": "You are a trading copilot.",
        }

    def test_structured_prompt_path_uses_custom_prompt(self, tmp_path):
        engine = make_engine(tmp_path, system_prompt="Agent prompt")
        _store_bill(engine, "bill1", "electricity", "PG&E")

        built = engine._build_prompt("how much solar credit did I get?", use_rag=True)

        assert built["messages"][0]["content"] == "Agent prompt"

    def test_estimate_usage_counts_custom_prompt_length(self, tmp_path):
        short = make_engine(tmp_path, system_prompt="tiny")
        long = make_engine(tmp_path, system_prompt="x" * 4000)

        assert long.estimate_usage(history=[])["used_tokens"] > short.estimate_usage(
            history=[]
        )["used_tokens"]

    def test_empty_string_prompt_falls_back_to_default(self, tmp_path):
        engine = make_engine(tmp_path, system_prompt="")

        assert engine.system_prompt == ChatEngine.SYSTEM_PROMPT


class TestModelParams:
    def test_new_client_built_from_params(self, tmp_path):
        engine = ChatEngine(
            vault_path=str(tmp_path / "vault"),
            encryption_key=os.urandom(32),
            model="qwen3.8:27b-mlx",
            context_window_tokens=16384,
        )

        assert engine.ollama_client.model == "qwen3.8:27b-mlx"
        assert engine.ollama_client.context_window_tokens == 16384
        assert engine.model == "qwen3.8:27b-mlx"
        assert engine.estimate_usage(history=[])["budget_tokens"] == (
            16384 - RESERVED_TOKENS
        )

    def test_provided_client_is_overridden_in_place(self, tmp_path):
        client = FakeOllamaClient()
        engine = ChatEngine(
            ollama_client=client,
            vault_path=str(tmp_path / "vault2"),
            encryption_key=os.urandom(32),
            model="gemma4:31b-mlx",
            context_window_tokens=4096,
        )

        assert client.model == "gemma4:31b-mlx"
        assert client.context_window_tokens == 4096
        assert engine.model == "gemma4:31b-mlx"


class TestRagScopeCategories:
    def test_scope_filters_structured_records_all_categories(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["electricity"])
        _store_bill(engine, "power", "electricity", "PG&E")
        _store_bill(engine, "card", "credit_card", "Chase")

        records = engine.get_structured_records()

        assert [r["storage_key"] for r in records] == ["power"]

    def test_scope_filters_named_category_query(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["electricity"])
        _store_bill(engine, "power", "electricity", "PG&E")
        _store_bill(engine, "card", "credit_card", "Chase")

        records = engine.get_structured_records(category="credit_card")

        assert records == []

    def test_scope_allows_records_within_scope(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["electricity"])
        _store_bill(engine, "power", "electricity", "PG&E")

        records = engine.get_structured_records(category="electricity")

        assert [r["storage_key"] for r in records] == ["power"]

    def test_scope_excludes_unclassified_documents(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["electricity"])
        engine.vault.store_data("note", {"metadata": {}, "text_content": "note"})

        assert engine.get_structured_records() == []

    def test_in_scope_category_keeps_plain_where(self, tmp_path):
        engine = make_engine(
            tmp_path, rag_scope_categories=["electricity", "credit_card"]
        )
        engine.rag_engine = FakeRagEngine(context="ctx")

        engine._build_prompt("how much solar credit did I get?", use_rag=True)

        assert engine.rag_engine.last_where == {"category": "electricity"}

    def test_out_of_scope_category_falls_back_to_scope_in_filter(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["credit_card"])
        engine.rag_engine = FakeRagEngine(context="ctx")

        engine._build_prompt("how much solar credit did I get?", use_rag=True)

        assert engine.rag_engine.last_where == {"category": {"$in": ["credit_card"]}}

    def test_no_category_with_scope_uses_in_filter(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["credit_card"])
        engine.rag_engine = FakeRagEngine(context="ctx")

        engine._build_prompt("what's in my note?", use_rag=True)

        assert engine.rag_engine.last_where == {"category": {"$in": ["credit_card"]}}

    def test_no_scope_keeps_unscoped_behavior(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.rag_engine = FakeRagEngine(context="ctx")

        engine._build_prompt("what's in my note?", use_rag=True)

        assert engine.rag_engine.last_where is None

    def test_provider_composes_with_in_scope_category(self, tmp_path):
        engine = make_engine(tmp_path, rag_scope_categories=["credit_card"])
        _store_bill(
            engine, "chase_meta", "credit_card", "Chase", with_extraction=False
        )
        engine.rag_engine = FakeRagEngine(context="card text")

        engine._build_prompt("what did I spend on my Chase credit card?", use_rag=True)

        assert engine.rag_engine.last_where == {
            "$and": [{"category": "credit_card"}, {"provider": "Chase"}]
        }
