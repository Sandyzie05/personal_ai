"""Unit tests for src.ai_engine.chroma_store metadata handling.

Uses a temp persist_directory and no embedding_generator (ChromaDB's local
default embedding function), so these run fully offline - no Ollama needed.
"""

import pytest

from src.ai_engine.chroma_store import ChromaStore, document_id, distance_to_similarity


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
    """Regression: `metadatas=[]` used to zip() to zero pairs and silently
    drop every document instead of getting default metadata."""
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


def test_retrieve_relevant_where_filter_scopes_results(store):
    store.add_documents(
        ["electric bill text", "gas bill text"],
        [{"category": "electricity"}, {"category": "gas"}],
    )

    results = store.retrieve_relevant(
        "bill text", k=5, where={"category": "electricity"}
    )

    assert len(results) == 1
    assert results[0]["content"] == "electric bill text"


def test_retrieve_relevant_defaults_similarity_score_field(store):
    """Each result now carries both the raw `distance` and a 0..1 `score`
    (similarity), so callers/tests read the "how relevant" intuition."""
    store.add_documents(
        ["electric bill monthly charges", "gas bill therm"],
        [{"storage_key": "e"}, {"storage_key": "g"}],
    )

    results = store.retrieve_relevant("electric bill", k=2, min_relevance=0.0)

    assert results
    assert all(0.0 <= r["score"] <= 1.0 for r in results)
    assert all("distance" in r for r in results)
    # Results come back most-similar-first.
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_retrieve_relevant_min_relevance_filters_irrelevant(store):
    """The core anti-hallucination lever: a near-miss (irrelevant) chunk must
    be dropped by the similarity threshold so the model can't answer from it."""
    store.add_documents(
        [
            "electric bill monthly kilowatt charges",
            "a sentence about the ocean and a swimming sea turtle",
        ],
        [{"storage_key": "e"}, {"storage_key": "z"}],
    )

    with_threshold = store.retrieve_relevant("electric bill", k=2, min_relevance=0.30)
    no_threshold = store.retrieve_relevant("electric bill", k=2, min_relevance=0.0)

    # Without the threshold, k=2 returns everything (incl. the irrelevant one).
    assert len(no_threshold) == 2
    # With the threshold, the "sea turtle" chunk is filtered out; the relevant
    # "electric bill" chunk survives.
    returned_keys = [r["metadata"].get("storage_key") for r in with_threshold]
    assert "z" not in returned_keys
    assert "e" in returned_keys
    assert all(r["score"] >= 0.30 for r in with_threshold)


def test_retrieve_relevant_negative_threshold_disabled(store):
    """A threshold < 0 disables the cut entirely (returns raw top-k)."""
    store.add_documents(
        ["electric bill", "ocean and a sea turtle"],
        [{"storage_key": "e"}, {"storage_key": "z"}],
    )

    results = store.retrieve_relevant("electric bill", k=2, min_relevance=-1.0)

    assert len(results) == 2


def test_retrieve_relevant_dedup_by_document(store):
    """max_per_document caps chunks per source doc so one document can't crowd
    out the others. Two chunks share storage_key 'a'; they collapse to 1 when
    max_per_document=1."""
    store.add_documents(
        [
            "electric bill part one charges",
            "electric bill part two usage",
            "gas bill",
        ],
        [
            {"storage_key": "a", "chunk": "1"},
            {"storage_key": "a", "chunk": "2"},
            {"storage_key": "b", "chunk": "1"},
        ],
    )

    # max_per_document=1: at most one 'a' chunk + the 'b' chunk.
    limited = store.retrieve_relevant(
        "electric bill charges usage", k=5, min_relevance=0.0, max_per_document=1
    )
    storage_keys = [r["metadata"].get("storage_key") for r in limited]
    assert storage_keys.count("a") <= 1

    # max_per_document=2 keeps both 'a' chunks.
    relaxed = store.retrieve_relevant(
        "electric bill charges usage", k=5, min_relevance=0.0, max_per_document=2
    )
    relaxed_keys = [r["metadata"].get("storage_key") for r in relaxed]
    assert relaxed_keys.count("a") <= 2


def test_dedup_keeps_chunks_without_document_identity():
    """Chunks with no storage_key can't be grouped, so dedupe must never
    silently drop them."""
    docs = [
        {"content": "a", "metadata": {}, "score": 0.9},
        {"content": "b", "metadata": None, "score": 0.8},
    ]
    out = ChromaStore._dedupe_by_document(docs, max_per_document=1)
    assert len(out) == 2
    assert [d["content"] for d in out] == ["a", "b"]


def test_document_id_uses_storage_key():
    assert document_id({"storage_key": "doc1"}) == "doc1"
    assert document_id({"category": "electricity"}) is None
    assert document_id(None) is None
    assert document_id({}) is None


def test_distance_to_similarity_conversion():
    assert distance_to_similarity(0.0) == 1.0
    assert distance_to_similarity(1.0) == 0.0
    # Clamps out-of-range distances into 0..1.
    assert distance_to_similarity(2.0) == 0.0
    assert distance_to_similarity(-0.5) == 1.0


def test_retrieve_relevant_records_last_retrieval(store):
    """last_retrieval() exposes the most recent results for diagnostics."""
    store.add_documents(
        ["electric bill charges"],
        [{"storage_key": "e"}],
    )

    results = store.retrieve_relevant("electric bill", k=3, min_relevance=0.0)

    assert store.last_retrieval() == results
    # Defensive copy: mutations don't leak back into the store.
    store.last_retrieval().append({"content": "x"})
    assert len(store.last_retrieval()) == len(results)
