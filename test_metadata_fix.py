#!/usr/bin/env python3
"""Test script to verify metadata fix for ChromaDB."""

import sys

sys.path.insert(0, "src")

from src.ai_engine.chroma_store import ChromaStore


def test_empty_dict_metadata():
    """Test that empty/missing metadata still gets a non-empty dict (ChromaDB rejects {})."""
    store = ChromaStore(collection_name="test_empty_dict")

    # Test 1: List with empty dict
    print("Test 1: [{}] with 1 doc")
    store.add_documents(["test"], [{}])
    docs = store.get_all()
    assert len(docs) == 1
    assert docs[0]["metadata"] == {"chunk": "1"}
    print("  OK Empty dict gets a chunk key so metadata is never empty")
    store.clear()

    # Test 2: List with multiple empty dicts
    print("\nTest 2: [{}, {}, {}] with 3 docs")
    store.add_documents(["a", "b", "c"], [{}, {}, {}])
    docs = store.get_all()
    assert len(docs) == 3
    for doc in docs:
        assert doc["metadata"] == {"chunk": "1"}
    print("  OK All empty dicts handled")
    store.clear()

    # Test 3: None metadatas
    print("\nTest 3: None metadatas")
    store.add_documents(["x", "y"], None)
    docs = store.get_all()
    assert len(docs) == 2
    for doc in docs:
        assert doc["metadata"] == {"chunk": "1"}
    print("  OK None handled correctly")
    store.clear()

    # Test 4: Empty list
    print("\nTest 4: Empty list []")
    store.add_documents(["a", "b"], [])
    docs = store.get_all()
    assert len(docs) == 2
    for doc in docs:
        assert doc["metadata"] == {"chunk": "1"}
    print("  OK Empty list handled correctly")
    store.clear()

    # Test 5: Mix of empty and non-empty
    print("\nTest 5: Mix [{}, {'key': 'val'}]")
    store.add_documents(["a", "b"], [{}, {"key": "val"}])
    docs = store.get_all()
    assert docs[0]["metadata"] == {"chunk": "1"}
    assert docs[1]["metadata"] == {"key": "val", "chunk": "1"}
    print("  OK Mixed metadata handled correctly")
    store.clear()

    print("\nAll tests passed!")


if __name__ == "__main__":
    test_empty_dict_metadata()
