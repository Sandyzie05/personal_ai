#!/usr/bin/env python3
"""Test script to verify metadata fix for ChromaDB."""

import sys
sys.path.insert(0, 'src')

from src.ai_engine.chroma_store import ChromaStore

def test_empty_dict_metadata():
    """Test that empty dicts are filtered."""
    store = ChromaStore(collection_name="test_empty_dict")
    
    # Test 1: List with empty dict
    print("Test 1: [{}] with 1 doc")
    store.add_documents(["test"], [{}])
    docs = store.get_all()
    assert len(docs) == 1
    assert docs[0]['metadata'] == {'_empty': True}
    print("  OK Empty dict converted to {'_empty': True}")
    store.clear()
    
    # Test 2: List with multiple empty dicts
    print("\nTest 2: [{}, {}, {}] with 3 docs")
    store.add_documents(["a", "b", "c"], [{}, {}, {}])
    docs = store.get_all()
    assert len(docs) == 3
    for doc in docs:
        assert doc['metadata'] == {'_empty': True}
    print("  OK All empty dicts converted")
    store.clear()
    
    # Test 3: None metadatas
    print("\nTest 3: None metadatas")
    store.add_documents(["x", "y"], None)
    docs = store.get_all()
    assert len(docs) == 2
    for doc in docs:
        assert doc['metadata'] == {'_empty': True}
    print("  OK None handled correctly")
    store.clear()
    
    # Test 4: Empty list
    print("\nTest 4: Empty list []")
    store.add_documents(["a", "b"], [])
    docs = store.get_all()
    assert len(docs) == 2
    for doc in docs:
        assert doc['metadata'] == {'_empty': True}
    print("  OK Empty list handled correctly")
    store.clear()
    
    # Test 5: Mix of empty and non-empty
    print("\nTest 5: Mix [{}, {'key': 'val'}]")
    store.add_documents(["a", "b"], [{}, {"key": "val"}])
    docs = store.get_all()
    assert docs[0]['metadata'] == {'_empty': True}
    assert docs[1]['metadata'] == {'key': 'val'}
    print("  OK Mixed metadata handled correctly")
    store.clear()
    
    print("\nAll tests passed!")

if __name__ == "__main__":
    test_empty_dict_metadata()
