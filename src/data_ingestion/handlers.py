"""
File Upload Handler for Personal AI System

Handles file uploads, encryption, text extraction, and vault storage
"""

import os
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from .parsers.csv_parser import CSVParser
from .parsers.pdf_parser import PDFParser
from .parsers.docx_parser import DOCXParser
from .parsers.health_parser import HealthXMLParser
from .parsers.financial_parser import FinancialCSVParser
from ..data_vault.vault import DataVault
from ..security.encryption import AEADEncryption


class IngestionError(Exception):
    """Custom exception for data ingestion operations."""
    pass


class FileUploadHandler:
    """Handles file uploads with encryption and text extraction."""
    
    SUPPORTED_FORMATS = {
        '.csv': 'csv',
        '.pdf': 'pdf',
        '.docx': 'docx',
        '.xml': 'health',
    }
    
    def __init__(self, vault: DataVault, encryption_key: Optional[bytes] = None):
        self.vault = vault
        if encryption_key:
            self._encryption = AEADEncryption(key=encryption_key)
        else:
            self._encryption = None
    
    def handle_upload(self, file_path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Handle file upload: encrypt, extract text, store in vault.
        
        Args:
            file_path: Path to the file to upload
            metadata: Optional metadata to associate with the file
            
        Returns:
            Dictionary with upload results including storage key and extracted content
        """
        try:
            file_path = Path(file_path)
            
            if not file_path.exists():
                raise IngestionError(f"File not found: {file_path}")
            
            # Determine file type
            file_ext = file_path.suffix.lower()
            file_type = self.SUPPORTED_FORMATS.get(file_ext)
            
            if file_type is None:
                raise IngestionError(f"Unsupported file format: {file_ext}")
            
            # Read original file content
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # Extract text content
            text_content = self._extract_text(file_path, file_type)
            
            # Prepare metadata
            file_stat = file_path.stat()
            upload_metadata = {
                'original_filename': file_path.name,
                'file_type': file_type,
                'file_size': file_stat.st_size,
                'upload_timestamp': datetime.now().isoformat(),
                'content_type': 'text',
                **(metadata or {})
            }
            
            # Create combined data for vault
            data_to_store = {
                'metadata': upload_metadata,
                'text_content': text_content,
                'raw_content_b64': self._encode_content(file_content)
            }
            
            # Store in vault (automatically encrypted)
            storage_key = self._generate_storage_key(file_path)
            self.vault.store_data(storage_key, data_to_store, encrypt=True)
            
            return {
                'success': True,
                'storage_key': storage_key,
                'file_type': file_type,
                'text_length': len(text_content),
                'metadata': upload_metadata
            }
            
        except Exception as e:
            raise IngestionError(f"Upload failed: {str(e)}")
    
    def handle_multiple_uploads(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Handle multiple file uploads."""
        results = []
        for path in file_paths:
            try:
                result = self.handle_upload(path)
                results.append(result)
            except IngestionError as e:
                results.append({
                    'success': False,
                    'error': str(e),
                    'file_path': path
                })
        return results
    
    def _extract_text(self, file_path: Path, file_type: str) -> str:
        """Extract text content based on file type."""
        try:
            if file_type == 'csv':
                parser = CSVParser()
                return parser.parse(file_path)
            elif file_type == 'pdf':
                parser = PDFParser()
                return parser.parse(file_path)
            elif file_type == 'docx':
                parser = DOCXParser()
                return parser.parse(file_path)
            elif file_type == 'health':
                parser = HealthXMLParser()
                return parser.parse(file_path)
            else:
                # Fallback: try to decode as text
                return file_path.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            raise IngestionError(f"Text extraction failed for {file_type}: {str(e)}")
    
    def _generate_storage_key(self, file_path: Path) -> str:
        """Generate unique storage key for the file."""
        base_name = file_path.stem
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        return f"{base_name}_{timestamp}_{unique_id}"
    
    def _encode_content(self, content: bytes) -> str:
        """Encode binary content as base64."""
        import base64
        return base64.b64encode(content).decode('utf-8')
    
    def get_uploaded_file(self, storage_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve uploaded file data from vault."""
        return self.vault.retrieve_data(storage_key)
    
    def list_uploaded_files(self) -> List[str]:
        """List all uploaded file keys."""
        return self.vault.list_keys()


def create_handler(vault: DataVault, encryption_key: Optional[bytes] = None) -> FileUploadHandler:
    """Factory function to create a FileUploadHandler."""
    return FileUploadHandler(vault=vault, encryption_key=encryption_key)
