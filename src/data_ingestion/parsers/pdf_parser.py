"""
PDF Parser for Personal AI System

Extracts text content from PDF files for RAG embedding
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
import re

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from src.data_extraction.bill_extractor import BillDataExtractor
    EXTRACTOR_AVAILABLE = True
except ImportError:
    EXTRACTOR_AVAILABLE = False


class PDFParser:
    """Parser for PDF files."""
    
    def __init__(self):
        if PdfReader is None:
            raise ImportError(
                "pypdf is required for PDF parsing. "
                "Install with: pip install pypdf"
            )
        
        self.extractor = None
        if EXTRACTOR_AVAILABLE:
            self.extractor = BillDataExtractor()
    
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
        """Clean and normalize extracted text."""
        full_text = "\n\n".join(text_parts)
        
        # Normalize whitespace
        full_text = re.sub(r'\s+', ' ', full_text)
        
        # Remove excessive newlines
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        
        # Strip leading/trailing whitespace
        return full_text.strip()
    
    def extract_metadata(self, file_path: Path) -> dict:
        """Extract PDF metadata."""
        if PdfReader is None:
            raise ImportError("pypdf not installed")
        
        reader = PdfReader(file_path)
        info = reader.metadata
        
        return {
            'title': info.title if info else None,
            'author': info.author if info else None,
            'subject': info.subject if info else None,
            'producer': info.producer if info else None,
            'pages': len(reader.pages),
        }
    
    def parse_structured(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Parse PDF and extract structured data if it's a T-Mobile bill.
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            Structured bill data dictionary if T-Mobile bill, None otherwise
        """
        if not self.extractor:
            return None
        
        try:
            text = self.parse(file_path)
            
            if not self.extractor.detect_bill_type(text):
                return None
            
            bill_data = self.extractor.extract(file_path)
            return bill_data.model_dump()
            
        except Exception:
            return None
