"""
Bill Data Extractor for T-Mobile PDF bills.
Extracts structured data using pattern matching for speed.
"""

import re
from pathlib import Path
from typing import List, Optional

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from .models import BillData, Charge, PaymentRecord


class BillDataExtractor:
    """Extracts structured data from T-Mobile bill PDFs."""
    
    def __init__(self):
        if PdfReader is None:
            raise ImportError(
                "pypdf is required for PDF parsing. "
                "Install with: pip install pypdf"
            )
        
        self.patterns = {
            'total_due': r'TOTAL\s+DUE\s*\$?(\d{1,3}(?:,\d{3})*\.?\d*)',
            'bill_date': r'Bill\s+issue\s+date\s+(\w+\s+\d{1,2},\s+\d{4})',
            'due_date': r'due\s+by\s+(\w+\s+\d{1,2},\s+\d{4})',
            'account': r'(?:Account\s*)?(\d{6,12})',
            'last_payment_date': r'paying\s+your\s+last\s+bill\s+of\s+\$?[\d,\.]+\s+on\s+(\w+\s+\d{1,2},\s+\d{4})',
            'last_payment_amount': r'last\s+bill\s+of\s+\$([\d,\.]+)',
            'payment_method': r'scheduled for (\w+ \d{1,2}, \d{4}) using (\w+)'
        }
    
    def detect_bill_type(self, text: str) -> bool:
        """Check if text matches T-Mobile bill patterns."""
        indicators = [
            'T-Mobile',
            'TOTAL DUE',
            'Bill issue date',
            'due by'
        ]
        return all(indicator.lower() in text.lower() for indicator in indicators)
    
    def extract_text(self, file_path: Path) -> str:
        """Extract text from PDF file."""
        reader = PdfReader(file_path)
        text_parts = []
        
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        
        return '\n'.join(text_parts)
    
    def _extract_amount(self, text: str, pattern: str) -> Optional[float]:
        """Extract numeric amount from text using pattern."""
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            amount_str = groups[0] if groups else None
            if amount_str:
                try:
                    return float(amount_str.replace(',', '').replace('$', ''))
                except ValueError:
                    pass
        return None
    
    def _extract_date(self, text: str, pattern: str) -> Optional[str]:
        """Extract date string from text using pattern."""
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            return groups[0] if groups else None
        return None
    
    def _extract_account(self, text: str) -> Optional[str]:
        """Extract account number from text."""
        match = re.search(self.patterns['account'], text, re.IGNORECASE)
        if match:
            groups = match.groups()
            return groups[0] if groups else None
        return None
    
    def parse_charges(self, text: str) -> List[Charge]:
        """Parse charges section from bill text."""
        charges = []
        
        charge_categories = {
            'PLANS': 'Monthly plans',
            'EQUIPMENT': 'Equipment purchases',
            'SERVICES': 'Additional services',
            'TOTAL': 'Total charges'
        }
        
        for category, description in charge_categories.items():
            pattern = rf'{category}[\s\S]*?\$([\d,\.]+)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    amount = float(match.group(1).replace(',', ''))
                    charges.append(Charge(
                        category=category,
                        amount=amount,
                        description=description
                    ))
                except ValueError:
                    continue
        
        return charges
    
    def parse_payment_history(self, text: str) -> List[PaymentRecord]:
        """Parse payment history from bill text."""
        payments = []
        
        payment_date = self._extract_date(
            text, 
            r'on\s+(\w+\s+\d{1,2},\s+\d{4})\.'
        )
        payment_amount = self._extract_amount(
            text, 
            r'last\s+bill\s+of\s+\$([\d,\.]+)'
        )
        
        if payment_date and payment_amount:
            payments.append(PaymentRecord(
                date=payment_date,
                amount=payment_amount,
                method='AutoPay'
            ))
        
        return payments
    
    def extract(self, file_path: Path) -> BillData:
        """
        Extract structured bill data from PDF.
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            BillData model with extracted information
        """
        text = self.extract_text(file_path)
        
        total_due = self._extract_amount(text, self.patterns['total_due']) or 0.0
        bill_date = self._extract_date(text, self.patterns['bill_date']) or ''
        due_date = self._extract_date(text, self.patterns['due_date']) or ''
        account_number = self._extract_account(text) or ''
        
        charges = self.parse_charges(text)
        payment_history = self.parse_payment_history(text)
        
        return BillData(
            total_due=total_due,
            bill_date=bill_date,
            due_date=due_date,
            account_number=account_number,
            charges=charges,
            payment_history=payment_history
        )
    
    def process(self, file_path: Path) -> Optional[BillData]:
        """
        Process PDF and return bill data if it's a T-Mobile bill.
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            BillData if T-Mobile bill detected, None otherwise
        """
        try:
            text = self.extract_text(file_path)
            
            if not self.detect_bill_type(text):
                return None
            
            return self.extract(file_path)
            
        except Exception as e:
            raise Exception(f"Bill extraction failed: {str(e)}")
