"""
T-Mobile Bill Data Extraction System

This package provides fast PDF bill extraction using pattern matching
instead of embeddings, avoiding the 10-30 second embedding delay.

Key components:
- BillDataExtractor: Pattern-based extraction (sub-second)
- BillStorage: SQLite storage with indexed queries
- BillData: Pydantic model for structured data
"""

from .models import BillData, Charge, PaymentRecord
from .bill_extractor import BillDataExtractor
from .bill_storage import BillStorage

__all__ = [
    'BillData',
    'Charge', 
    'PaymentRecord',
    'BillDataExtractor',
    'BillStorage'
]
