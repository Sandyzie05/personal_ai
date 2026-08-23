"""
Financial Data Parser for Personal AI System

Parses CSV files with date/amount fields for financial data extraction
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import csv
import re

try:
    from dateutil import parser as date_parser
    DATEUTIL_AVAILABLE = True
except ImportError:
    DATEUTIL_AVAILABLE = False


class FinancialCSVParser:
    """Parser for financial CSV files."""
    
    # Column name patterns for detection
    DATE_PATTERNS = [
        r'date', r'dated', r'time', r'timestamp', r'day', r'month', r'year'
    ]
    AMOUNT_PATTERNS = [
        r'amount', r'value', r'price', r'cost', r'balance', r'total',
        r'payment', r'income', r'expense', r'money'
    ]
    DESCRIPTION_PATTERNS = [
        r'description', r'name', r'title', r'memo', r'reference', r'transaction'
    ]
    
    def parse(self, file_path: Path) -> str:
        """
        Parse financial CSV file and extract structured data.
        
        Args:
            file_path: Path to the CSV file
            
        Returns:
            Formatted text content suitable for RAG
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            # Parse CSV
            lines = content.strip().split('\n')
            if not lines:
                return ""
            
            # Detect columns
            header = self._parse_line(lines[0])
            column_types = self._detect_column_types(header)
            
            # Parse rows
            transactions = []
            for line in lines[1:]:
                if line.strip():
                    row = self._parse_line(line)
                    if len(row) == len(header):
                        transaction = self._build_transaction(header, row, column_types)
                        if transaction:
                            transactions.append(transaction)
            
            return self._format_transactions(transactions)
            
        except Exception as e:
            raise Exception(f"Financial CSV parsing failed: {str(e)}")
    
    def _parse_line(self, line: str) -> List[str]:
        """Parse a single CSV line."""
        try:
            reader = csv.reader(csv.StringIO(line))
            return next(reader)
        except:
            return line.split(',')
    
    def _detect_column_types(self, header: List[str]) -> Dict[str, str]:
        """Detect the type of each column based on name patterns."""
        types = {}
        header_lower = [h.lower() for h in header]
        
        for i, col in enumerate(header_lower):
            for pattern in self.DATE_PATTERNS:
                if re.search(pattern, col):
                    types[i] = 'date'
                    break
            else:
                for pattern in self.AMOUNT_PATTERNS:
                    if re.search(pattern, col):
                        types[i] = 'amount'
                        break
                else:
                    for pattern in self.DESCRIPTION_PATTERNS:
                        if re.search(pattern, col):
                            types[i] = 'description'
                            break
                    else:
                        types[i] = 'other'
        
        return types
    
    def _build_transaction(
        self, header: List[str], row: List[str], column_types: Dict[int, str]
    ) -> Optional[Dict[str, Any]]:
        """Build a transaction dict from row data."""
        transaction = {}
        
        for i, value in enumerate(row):
            col_type = column_types.get(i, 'other')
            header_name = header[i].lower().replace(' ', '_')
            
            if col_type == 'date':
                try:
                    parsed_date = self._parse_date(value)
                    if parsed_date:
                        transaction['date'] = parsed_date
                except:
                    pass
            elif col_type == 'amount':
                try:
                    amount = float(value.replace(',', '').replace('$', '').replace('€', ''))
                    transaction['amount'] = amount
                except:
                    pass
            elif col_type == 'description' or col_type == 'other':
                if 'description' not in transaction:
                    transaction['description'] = value
        
        # Require at least date and amount
        if 'date' in transaction and 'amount' in transaction:
            return transaction
        
        return None
    
    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse date string to ISO format."""
        if not DATEUTIL_AVAILABLE:
            # Fallback: basic parsing
            match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            return None
        
        try:
            dt = date_parser.parse(date_str)
            return dt.isoformat()
        except:
            return None
    
    def _format_transactions(self, transactions: List[Dict[str, Any]]) -> str:
        """Format transactions as text for RAG."""
        if not transactions:
            return "No financial transactions found."
        
        lines = [f"Financial Data - {len(transactions)} transactions"]
        lines.append("")
        
        # Calculate summary
        total = sum(t.get('amount', 0) for t in transactions)
        avg = total / len(transactions) if transactions else 0
        
        lines.append(f"Summary:")
        lines.append(f"- Total: ${total:,.2f}")
        lines.append(f"- Average: ${avg:,.2f}")
        lines.append(f"- Date range: {self._get_date_range(transactions)}")
        lines.append("")
        lines.append("Transactions:")
        lines.append("")
        
        # List transactions (limit to first 50)
        for i, txn in enumerate(transactions[:50], 1):
            date_str = txn.get('date', 'Unknown')
            amount = txn.get('amount', 0)
            desc = txn.get('description', '')
            sign = '+' if amount >= 0 else ''
            lines.append(
                f"{i}. [{date_str}] {sign}${amount:,.2f} - {desc}"
            )
        
        if len(transactions) > 50:
            lines.append("")
            lines.append(f"... and {len(transactions) - 50} more transactions")
        
        return "\n".join(lines)
    
    def _get_date_range(self, transactions: List[Dict[str, Any]]) -> str:
        """Get date range from transactions."""
        dates = [t.get('date') for t in transactions if t.get('date')]
        if not dates:
            return "Unknown"
        
        dates.sort()
        return f"{dates[0]} to {dates[-1]}"
    
    def parse_to_list(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse and return list of transaction dictionaries."""
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        lines = content.strip().split('\n')
        if not lines:
            return []
        
        header = self._parse_line(lines[0])
        column_types = self._detect_column_types(header)
        
        transactions = []
        for line in lines[1:]:
            if line.strip():
                row = self._parse_line(line)
                if len(row) == len(header):
                    txn = self._build_transaction(header, row, column_types)
                    if txn:
                        transactions.append(txn)
        
        return transactions
