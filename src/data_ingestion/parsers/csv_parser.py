"""
CSV Parser for Personal AI System

Parses CSV files with header + row parsing for structured data extraction
"""

import csv
from pathlib import Path
from typing import List, Dict, Any, Optional
from io import StringIO


class CSVParser:
    """Parser for CSV files."""
    
    def parse(self, file_path: Path) -> str:
        """
        Parse CSV file and extract text content.
        
        Args:
            file_path: Path to the CSV file
            
        Returns:
            Text content formatted for RAG embedding
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            # Parse CSV
            lines = content.strip().split('\n')
            if not lines:
                return ""
            
            # Parse header and rows
            header = self._parse_line(lines[0])
            rows = []
            
            for line in lines[1:]:
                if line.strip():
                    row = self._parse_line(line)
                    if row:
                        rows.append(row)
            
            # Format for RAG
            return self._format_csv_content(header, rows)
            
        except Exception as e:
            raise Exception(f"CSV parsing failed: {str(e)}")
    
    def _parse_line(self, line: str) -> List[str]:
        """Parse a single CSV line handling quoted fields."""
        try:
            reader = csv.reader(StringIO(line))
            return next(reader)
        except:
            return line.split(',')
    
    def _format_csv_content(self, header: List[str], rows: List[List[str]]) -> str:
        """Format CSV content as structured text."""
        if not header:
            return ""
        
        lines = []
        lines.append(f"CSV File with {len(rows)} rows")
        lines.append(f"Columns: {', '.join(header)}")
        lines.append("")
        
        for i, row in enumerate(rows, 1):
            row_dict = dict(zip(header, row))
            row_text = ", ".join(f"{k}={v}" for k, v in row_dict.items())
            lines.append(f"Row {i}: {row_text}")
        
        return "\n".join(lines)
    
    def parse_to_dicts(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse CSV and return list of dictionaries."""
        rows = []
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows
