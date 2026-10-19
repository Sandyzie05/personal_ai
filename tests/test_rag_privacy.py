"""RAG diagnostics retain metrics, never synthetic private content."""

import logging
from unittest.mock import Mock

import pytest

from src.ai_engine.rag_engine import RAGEngine, RAGEngineError, _ChromaStoreError


def test_retrieval_logs_only_counts_and_scores(caplog):
    store = Mock()
    docs = [
        {
            "content": "document-content-canary",
            "metadata": {
                "provider": "provider-canary",
                "original_filename": "filename-canary",
                "category": "category-canary",
            },
            "score": 0.84,
        },
        {"content": "second-content-canary", "score": 0.42},
    ]
    store.retrieve_relevant.return_value = docs
    engine = RAGEngine(embedding_generator=Mock(), chroma_store=store)

    with caplog.at_level(logging.INFO):
        context = engine.get_context_for_query("query-canary")

    assert "document-content-canary" in context
    assert "provider-canary" in context
    assert "canary" not in caplog.text
    assert "2 chunk(s)" in caplog.text
    assert "similarities: 0.84, 0.42" in caplog.text


def test_empty_retrieval_logs_threshold_not_query(caplog):
    store = Mock()
    store.retrieve_relevant.return_value = []
    engine = RAGEngine(
        embedding_generator=Mock(), chroma_store=store, min_relevance=0.25
    )

    with caplog.at_level(logging.INFO):
        assert engine.get_context_for_query("query-canary") == ""

    assert "canary" not in caplog.text
    assert "0 chunk(s)" in caplog.text
    assert "min 0.25" in caplog.text
    assert "ungrounded/not-found" in caplog.text


@pytest.mark.parametrize("many", [False, True])
def test_ingestion_errors_do_not_leak_store_exception(caplog, many):
    store = Mock()
    private_error = "private-error-" + "canary"
    document = "document-" + "canary"
    store.add_documents.side_effect = _ChromaStoreError(private_error)
    engine = RAGEngine(embedding_generator=Mock(), chroma_store=store)

    with caplog.at_level(logging.ERROR):
        try:
            if many:
                engine.add_documents([document])
            else:
                engine.add_document(document)
        except RAGEngineError:
            logging.getLogger(__name__).exception("Indexing failed")
        else:
            pytest.fail("Expected ingestion to fail")

    assert private_error not in caplog.text
    assert document not in caplog.text
    assert "Failed to add document" in caplog.text
