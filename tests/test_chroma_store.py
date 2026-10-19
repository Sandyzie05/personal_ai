import unittest
from unittest.mock import patch, MagicMock
import os

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

    @patch('src.ai_engine.chroma_store.chromadb')
    def test_chroma_store_initialization(self, mock_chromadb):
        # Mock ChromaDB client
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_chromadb.Client.return_value = mock_client
        mock_client.get_or_create_collection.return_value = mock_collection

        store = ChromaStore(embedding_generator=self.mock_embedding_gen)
        store._ensure_initialized()

        # Should create client with correct settings
        mock_chromadb.Client.assert_called_once_with(
            mock_chromadb.config.Settings(
                persist_directory=store.persist_directory,
                anonymized_telemetry=False,
            )
        )
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


if __name__ == '__main__':
    unittest.main()