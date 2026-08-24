"""Unit tests for src.ai_engine.chroma_store metadata handling.

Uses a temp persist_directory and no embedding_generator (ChromaDB's local
default embedding function), so these run fully offline - no Ollama needed.
"""

import pytest

from src.ai_engine.chroma_store import ChromaStore


@pytest.fixture(scope="module")
def chroma_dir(tmp_path_factory):
    # chromadb.Client() caches a process-wide singleton keyed by settings;
    # instantiating it with a different persist_directory per test raises
    # "instance already exists with different settings". Share one
    # directory for the whole module and isolate tests via collection_name
    # instead.
    return str(tmp_path_factory.mktemp("chroma"))


@pytest.fixture
def store(chroma_dir, request):
    return ChromaStore(
        collection_name=f"test_{request.node.name}",
        persist_directory=chroma_dir,
    )


def test_empty_dict_metadata_gets_chunk_key(store):
    store.add_documents(["test"], [{}])

    docs = store.get_all()

    assert len(docs) == 1
    assert docs[0]["metadata"] == {"chunk": "1"}


def test_multiple_empty_dicts(store):
    store.add_documents(["a", "b", "c"], [{}, {}, {}])

    docs = store.get_all()

    assert len(docs) == 3
    assert all(doc["metadata"] == {"chunk": "1"} for doc in docs)


def test_none_metadatas_defaults_per_document(store):
    store.add_documents(["x", "y"], None)

    docs = store.get_all()

    assert len(docs) == 2
    assert all(doc["metadata"] == {"chunk": "1"} for doc in docs)


def test_empty_list_metadatas_does_not_drop_documents(store):
    """Regression test: `metadatas=[]` used to zip() to zero pairs and
    silently drop every document instead of getting default metadata."""
    store.add_documents(["a", "b"], [])

    docs = store.get_all()

    assert len(docs) == 2
    assert all(doc["metadata"] == {"chunk": "1"} for doc in docs)


def test_mixed_empty_and_nonempty_metadata(store):
    store.add_documents(["a", "b"], [{}, {"key": "val"}])

    docs = store.get_all()

    assert docs[0]["metadata"] == {"chunk": "1"}
    assert docs[1]["metadata"] == {"key": "val", "chunk": "1"}


def test_clear_removes_all_documents(store):
    store.add_documents(["a", "b"], [{}, {}])

    store.clear()

    assert store.get_all() == []
