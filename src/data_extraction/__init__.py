"""Document category detection and structured extraction.

Generic, schema-driven pipeline for turning uploaded bills/statements into
queryable structured data - see categories.py, classifier.py,
schemas.py, and extractor.py.
"""

from .categories import CATEGORIES, Category, CategoryField, get_category, category_keys
from .classifier import classify_document
from .extractor import ExtractionError, extract_structured_data
from .schemas import ExtractedDocument, LineItem

__all__ = [
    "CATEGORIES",
    "Category",
    "CategoryField",
    "get_category",
    "category_keys",
    "classify_document",
    "ExtractedDocument",
    "ExtractionError",
    "extract_structured_data",
    "LineItem",
]
