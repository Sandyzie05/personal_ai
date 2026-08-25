"""
PDF Parser for Personal AI System

Extracts text content from PDF files for RAG embedding
"""

from pathlib import Path
from typing import List
import re

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


class PDFParser:
    """Parser for PDF files."""

    def __init__(self):
        if PdfReader is None:
            raise ImportError(
                "pypdf is required for PDF parsing. Install with: pip install pypdf"
            )

    def parse(self, file_path: Path) -> str:
        """
        Parse PDF file and extract text content.

        Args:
            file_path: Path to the PDF file

        Returns:
            Text content extracted from PDF
        """
        if PdfReader is None:
            raise ImportError("pypdf not installed")

        try:
            reader = PdfReader(file_path)
            text_parts = []

            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

            # Clean up text
            return self._clean_text(text_parts)

        except Exception as e:
            raise Exception(f"PDF parsing failed: {str(e)}")

    def _clean_text(self, text_parts: List[str]) -> str:
        """Clean and normalize extracted text.

        Only collapses horizontal whitespace (spaces/tabs) - newlines are
        preserved so line/paragraph boundaries survive for the RAG chunker
        (chroma_store._chunk_text splits on "\n"). Collapsing everything
        into one run-on string previously destroyed those boundaries,
        degrading chunk quality for multi-column bill layouts.
        """
        full_text = "\n\n".join(text_parts)

        # Normalize horizontal whitespace, keep newlines.
        full_text = re.sub(r"[ \t]+", " ", full_text)

        # Remove excessive blank lines.
        full_text = re.sub(r"\n{3,}", "\n\n", full_text)

        # Strip leading/trailing whitespace
        return full_text.strip()

    def extract_metadata(self, file_path: Path) -> dict:
        """Extract PDF metadata."""
        if PdfReader is None:
            raise ImportError("pypdf not installed")

        reader = PdfReader(file_path)
        info = reader.metadata

        return {
            "title": info.title if info else None,
            "author": info.author if info else None,
            "subject": info.subject if info else None,
            "producer": info.producer if info else None,
            "pages": len(reader.pages),
        }
