"""
File Upload Handler for Personal AI System

Handles file uploads, encryption, text extraction, and vault storage
"""

import csv
import uuid
from io import BytesIO, StringIO
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from .parsers.csv_parser import CSVParser
from .parsers import pdf_parser
from .parsers.docx_parser import DOCXParser
from .parsers.health_parser import HealthXMLParser
from ..data_vault.vault import DataVault
from ..security.encryption import AEADEncryption


class IngestionError(Exception):
    """Custom exception for data ingestion operations."""

    pass


class FileUploadHandler:
    """Handles file uploads with encryption and text extraction."""

    SUPPORTED_FORMATS = {
        ".csv": "csv",
        ".pdf": "pdf",
        ".docx": "docx",
        ".xml": "health",
        ".txt": "text",
        ".md": "text",
        ".json": "text",
    }

    def __init__(self, vault: DataVault, encryption_key: Optional[bytes] = None):
        self.vault = vault
        if encryption_key:
            self._encryption = AEADEncryption(key=encryption_key)
        else:
            self._encryption = None

    def _resolve_file_type(self, file_path: Path) -> str:
        """Look up the supported file_type for a path, raising if unsupported."""
        if not file_path.exists():
            raise IngestionError(f"File not found: {file_path}")

        file_type = self.SUPPORTED_FORMATS.get(file_path.suffix.lower())
        if file_type is None:
            raise IngestionError(f"Unsupported file format: {file_path.suffix.lower()}")
        return file_type

    def preview_text(self, file_path: str) -> str:
        """Extract text from a caller-supplied local path (CLI compatibility)."""
        path = Path(file_path)
        self._resolve_file_type(path)
        try:
            return self.preview_bytes(path.read_bytes(), path.name)
        except OSError as e:
            raise IngestionError(f"Could not read file: {e}") from e

    def _upload_name(self, filename: str) -> Path:
        """Treat an upload name only as a label, never as a filesystem path."""
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not name or name in {".", ".."} or "\x00" in name:
            raise IngestionError("Invalid upload filename")
        path = Path(name)
        if path.suffix.lower() not in self.SUPPORTED_FORMATS:
            raise IngestionError(f"Unsupported file format: {path.suffix.lower()}")
        return path

    def preview_bytes(self, content: bytes, filename: str) -> str:
        """Parse upload bytes without opening the supplied filename or staging on disk."""
        name = self._upload_name(filename)
        file_type = self.SUPPORTED_FORMATS[name.suffix.lower()]
        try:
            if file_type == "pdf":
                if pdf_parser.pymupdf is None or pdf_parser.pymupdf4llm is None:
                    raise ImportError("pymupdf4llm is required for PDF parsing")
                with pdf_parser.pymupdf.open(stream=content, filetype="pdf") as doc:
                    return pdf_parser.pymupdf4llm.to_markdown(doc).strip()
            if file_type == "docx":
                return DOCXParser().parse(BytesIO(content))
            if file_type == "health":
                return HealthXMLParser().parse(BytesIO(content))
            text = content.decode("utf-8")
            if file_type == "csv":
                rows = list(csv.reader(StringIO(text, newline=""), strict=True))
                rows = [row for row in rows if row]
                if not rows or not any(rows[0]):
                    raise ValueError("Missing CSV header")
                header, *records = rows
                if any(len(row) != len(header) for row in records):
                    raise ValueError("CSV row does not match header")
                return CSVParser()._format_csv_content(header, records)
            return text
        except Exception as e:
            raise IngestionError(f"Text extraction failed for {file_type}: {e}") from e

    def handle_upload(
        self, file_path: str, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Upload a caller-supplied local file (CLI compatibility)."""
        try:
            path = Path(file_path)
            self._resolve_file_type(path)
            return self.handle_upload_bytes(path.read_bytes(), path.name, metadata)
        except Exception as e:
            raise IngestionError(f"Upload failed: {e}") from e

    def handle_upload_bytes(
        self,
        content: bytes,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Parse in memory and encrypt original bytes plus extracted text in the vault."""
        try:
            name = self._upload_name(filename)
            file_type = self.SUPPORTED_FORMATS[name.suffix.lower()]
            text_content = self.preview_bytes(content, name.name)

            # Prepare metadata
            upload_metadata = {
                "original_filename": name.name,
                "file_type": file_type,
                "file_size": len(content),
                "upload_timestamp": datetime.now().isoformat(),
                "content_type": "text",
                **(metadata or {}),
            }

            # Create combined data for vault
            data_to_store = {
                "metadata": upload_metadata,
                "text_content": text_content,
                "raw_content_b64": self._encode_content(content),
            }

            # Store in vault (automatically encrypted)
            storage_key = self._generate_storage_key(name)
            self.vault.store_data(storage_key, data_to_store, encrypt=True)

            return {
                "success": True,
                "storage_key": storage_key,
                "file_type": file_type,
                "text_length": len(text_content),
                "metadata": upload_metadata,
            }

        except Exception as e:
            raise IngestionError(f"Upload failed: {str(e)}") from e

    def handle_multiple_uploads(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Handle multiple file uploads."""
        results = []
        for path in file_paths:
            try:
                result = self.handle_upload(path)
                results.append(result)
            except IngestionError as e:
                results.append({"success": False, "error": str(e), "file_path": path})
        return results

    def _generate_storage_key(self, file_path: Path) -> str:
        """Generate unique storage key for the file."""
        base_name = file_path.stem
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        return f"{base_name}_{timestamp}_{unique_id}"

    def _encode_content(self, content: bytes) -> str:
        """Encode binary content as base64."""
        import base64

        return base64.b64encode(content).decode("utf-8")

    def get_uploaded_file(self, storage_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve uploaded file data from vault."""
        return self.vault.retrieve_data(storage_key)

    def list_uploaded_files(self) -> List[str]:
        """List all uploaded file keys."""
        return self.vault.list_keys()


def create_handler(
    vault: DataVault, encryption_key: Optional[bytes] = None
) -> FileUploadHandler:
    """Factory function to create a FileUploadHandler."""
    return FileUploadHandler(vault=vault, encryption_key=encryption_key)
