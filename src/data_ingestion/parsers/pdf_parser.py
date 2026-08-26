"""
PDF Parser for Personal AI System

Extracts text content from PDF files for RAG embedding.

Uses pymupdf4llm (built on PyMuPDF) rather than pypdf. pypdf's text
extraction misjudges reading order on narrow table columns - exactly the
layout real bills use for line items/dollar amounts - and emits them
character-by-character (e.g. "5.00" becomes "5\n.\n0\n0"); see
https://github.com/py-pdf/pypdf/issues/3212 and
https://github.com/py-pdf/pypdf/issues/2330 for confirmed reports.
pymupdf4llm instead reconstructs tables and reading order into Markdown,
which also gives the RAG chunker (chroma_store._chunk_text) real
line/paragraph boundaries to split on.

This only extracts the embedded text layer - scanned/image-only PDFs with
no text layer will yield little to no text (no OCR fallback yet).
"""

from pathlib import Path

try:
    import pymupdf
    import pymupdf4llm
except ImportError:
    pymupdf = None
    pymupdf4llm = None


class PDFParser:
    """Parser for PDF files."""

    def __init__(self):
        if pymupdf4llm is None:
            raise ImportError(
                "pymupdf4llm is required for PDF parsing. Install with: "
                "pip install pymupdf4llm"
            )

    def parse(self, file_path: Path) -> str:
        """
        Parse PDF file and extract text content as Markdown.

        Args:
            file_path: Path to the PDF file

        Returns:
            Markdown text content extracted from PDF, with table structure
            and line/paragraph breaks preserved.
        """
        if pymupdf4llm is None:
            raise ImportError("pymupdf4llm not installed")

        try:
            return pymupdf4llm.to_markdown(str(file_path)).strip()
        except Exception as e:
            raise Exception(f"PDF parsing failed: {str(e)}")

    def extract_metadata(self, file_path: Path) -> dict:
        """Extract PDF metadata."""
        if pymupdf is None:
            raise ImportError("pymupdf not installed")

        with pymupdf.open(str(file_path)) as doc:
            info = doc.metadata or {}
            return {
                "title": info.get("title") or None,
                "author": info.get("author") or None,
                "subject": info.get("subject") or None,
                "producer": info.get("producer") or None,
                "pages": doc.page_count,
            }
