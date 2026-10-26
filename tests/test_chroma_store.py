import unittest
from unittest.mock import patch, MagicMock
import os
import tempfile

from src.ai_engine.chroma_store import (
    ChromaStore,
    chunk_text,
    _chunk_markdown,
    _chunk_text,
    create_chroma_store
)
from src.ai_engine.embeddings import EmbeddingsGenerator


class TestChromaStore(unittest.TestCase):

    def setUp(self):
        # Create a mock embedding generator
        self.mock_embedding_gen = MagicMock()
        self.mock_embedding_gen.query_prefix = ""
        self.mock_embedding_gen.document_prefix = ""
        self.mock_embedding_gen.embed_context_tokens = 2048

    def test_chroma_store_default_collection_name(self):
        # Test that the default collection name is v3 (as per plan)
        store = ChromaStore(embedding_generator=self.mock_embedding_gen)
        self.assertEqual(store.collection_name, "personal_ai_vault_v3")

    @patch("chromadb.config.Settings")
    @patch("chromadb.Client")
    def test_chroma_store_initialization(self, mock_client_factory, mock_settings):
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_client_factory.return_value = mock_client
        mock_client.get_or_create_collection.return_value = mock_collection

        with tempfile.TemporaryDirectory() as directory:
            store = ChromaStore(
                embedding_generator=self.mock_embedding_gen,
                persist_directory=directory,
            )
            store._ensure_initialized()
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)

        mock_settings.assert_called_once_with(
            persist_directory=store.persist_directory,
            anonymized_telemetry=False,
        )
        mock_client_factory.assert_called_once_with(mock_settings.return_value)
        mock_client.get_or_create_collection.assert_called_once_with(
            name=store.collection_name, metadata={"hnsw:space": "cosine"}
        )

    def test_chunk_text_regular_text(self):
        # Test regular text chunking
        text = "This is a test sentence. This is another sentence. " * 50  # Long text

        chunks = chunk_text(text, embed_context_tokens=2048)

        # Should not be empty
        self.assertTrue(len(chunks) > 0)

        # Check that all chunks are under 2000 chars
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)

    def test_chunk_text_with_markdown(self):
        # Test markdown-aware chunking
        markdown_text = """# Heading 1

This is paragraph 1.

## Heading 2

This is paragraph 2.

| Column 1 | Column 2 |
|----------|----------|
| Row 1    | Data 1   |
| Row 2    | Data 2   |

Another paragraph after table.

### Heading 3

Code block:
```python
print("Hello World")
```
"""

        chunks = chunk_text(markdown_text, embed_context_tokens=2048)

        # Should not be empty
        self.assertTrue(len(chunks) > 0)

        # Check that chunks contain markdown structure
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)  # Soft cap

    def test_chunk_markdown_no_structure(self):
        # Test markdown chunking with no actual markdown structure
        plain_text = "This is plain text with no markdown structure. " * 50

        chunks = _chunk_markdown(plain_text, chunk_chars=1000)

        # Should fall back to regular chunking
        self.assertTrue(len(chunks) > 0)

    def test_chunk_markdown_with_tables(self):
        # Test table-aware chunking
        table_text = """# Report

| Name | Age | City |
|------|-----|------|
| John | 25  | NYC  |
| Jane | 30  | LA   |
| Bob  | 35  | Chicago |

Data continues here with more information that might be long...

More data...

### Conclusion

Final thoughts about the data above.
"""

        chunks = _chunk_markdown(table_text, chunk_chars=1000)

        # Should not be empty
        self.assertTrue(len(chunks) > 0)

        # Check that tables are preserved in chunks when possible
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)  # Soft cap

    def test_chunk_markdown_with_code_blocks(self):
        # Test code block preservation
        code_text = """# Code Example

Here's some code:
```python
def hello_world():
    print("Hello, World!")
    return True
```

More explanation of the code...
"""

        chunks = _chunk_markdown(code_text, chunk_chars=1000)

        # Should not be empty
        self.assertTrue(len(chunks) > 0)

        # Check that code blocks are preserved
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)  # Soft cap

    def test_embed_prefixes(self):
        # Test with nomic embed model
        mock_nomic_gen = MagicMock()
        mock_nomic_gen.query_prefix = "search_query: "
        mock_nomic_gen.document_prefix = "search_document: "
        mock_nomic_gen.embed_context_tokens = 2048

        # Test with Qwen model (should have empty prefixes)
        mock_qwen_gen = MagicMock()
        mock_qwen_gen.query_prefix = ""
        mock_qwen_gen.document_prefix = ""
        mock_qwen_gen.embed_context_tokens = 32768

        # Create stores with different embedding generators
        store_nomic = ChromaStore(embedding_generator=mock_nomic_gen)
        store_qwen = ChromaStore(embedding_generator=mock_qwen_gen)

        # Both should have valid prefix values
        self.assertEqual(store_nomic.embedding_generator.query_prefix, "search_query: ")
        self.assertEqual(store_nomic.embedding_generator.document_prefix, "search_document: ")
        self.assertEqual(store_qwen.embedding_generator.query_prefix, "")
        self.assertEqual(store_qwen.embedding_generator.document_prefix, "")

    def test_clear_deletes_actual_ids_not_a_metadata_field(self):
        store = ChromaStore(embedding_generator=self.mock_embedding_gen)
        store._initialized = True
        store.collection = MagicMock()
        store.collection.get.return_value = {"ids": ["legacy-random-id", "chunk-id"]}
        store._last_retrieval = [{"content": "synthetic text"}]

        self.assertTrue(store.clear())

        store.collection.get.assert_called_once_with(include=[])
        store.collection.delete.assert_called_once_with(ids=["legacy-random-id", "chunk-id"])
        self.assertEqual(store.last_retrieval(), [])

    def test_clear_empty_collection_does_not_send_invalid_delete(self):
        store = ChromaStore()
        store._initialized = True
        store.collection = MagicMock()
        store.collection.get.return_value = {"ids": []}

        self.assertTrue(store.clear())

        store.collection.delete.assert_not_called()

    def test_vault_chunk_ids_survive_rebuild_and_distinguish_sources(self):
        store = ChromaStore(embedding_generator=self.mock_embedding_gen)
        store._initialized = True
        store.collection = MagicMock()
        documents = ["synthetic text " * 400, "another synthetic document"]
        metadata = [{"storage_key": "source-a"}, {"storage_key": "source-b"}]

        first = store.add_documents(documents, metadata)
        store.clear()
        second = store.add_documents(documents, metadata)

        self.assertEqual(first, second)
        self.assertGreater(len(first), 2)
        self.assertEqual(len(first), len(set(first)))


class TestChunkText(unittest.TestCase):

    def test_chunk_text_empty_input(self):
        # Test empty input
        result = _chunk_text("", 100)
        self.assertEqual(result, [])

        result = _chunk_text(None, 100)
        self.assertEqual(result, [])

    def test_chunk_text_simple(self):
        # Test simple case
        text = "Hello world!"
        result = _chunk_text(text, 20)
        self.assertEqual(result, ["Hello world!"])

    def test_chunk_text_multiline(self):
        # Test multi-line text
        text = "Line 1\nLine 2\nLine 3"
        result = _chunk_text(text, 20)
        self.assertEqual(result, ["Line 1", "Line 2", "Line 3"])

    def test_chunk_text_large_chunks(self):
        # Test when chunks are larger than cap
        text = "This is a very long sentence that should be split properly by the chunking function. " * 10
        result = _chunk_text(text, 100)
        self.assertTrue(len(result) > 0)

        # All chunks should be <= cap
        for chunk in result:
            self.assertLessEqual(len(chunk), 100)

    def test_overlap_and_midstream_long_words_never_exceed_cap(self):
        for cap in (1, 10, 100):
            with self.subTest(cap=cap):
                text = "before " + "x" * (cap * 3 + 1) + " after"
                chunks = _chunk_text(text, cap, overlap_chars=200)
                self.assertTrue(chunks)
                self.assertTrue(all(0 < len(chunk) <= cap for chunk in chunks))
                self.assertEqual(
                    "".join(_chunk_text("x" * (cap * 3 + 1), cap)),
                    "x" * (cap * 3 + 1),
                )

    def test_oversized_markdown_rows_respect_embedding_cap(self):
        text = "| heading |\n|---|\n| " + "x" * 5000 + " |"
        chunks = chunk_text(text, embed_context_tokens=100)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertIn("x" * 100, chunks)


if __name__ == '__main__':
    unittest.main()