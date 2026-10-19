"""Synthetic, offline coverage for memory-backed ingestion and CLI compatibility."""

import base64
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from src.data_ingestion.handlers import FileUploadHandler, IngestionError
from src.data_ingestion.parsers import pdf_parser


@pytest.mark.parametrize("extension", ["txt", "md", "json", "csv", "pdf", "docx", "xml"])
def test_supported_uploads_parse_in_memory_and_encrypt_originals(extension):
    text = "SYNTHETIC-INGESTION-CANARY"
    content = text.encode()
    if extension == "json":
        content = ('{"note": "' + text + '"}').encode()
    elif extension == "csv":
        content = f'provider,amount\r\n"{text}\nsecond line",42\r\n'.encode()
    elif extension == "pdf":
        import pymupdf

        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), text)
            content = doc.tobytes()
    elif extension == "docx":
        from docx import Document

        doc = Document()
        doc.add_paragraph(text)
        output = BytesIO()
        doc.save(output)
        content = output.getvalue()
    elif extension == "xml":
        content = (
            f'<HealthData><Record type="HeartRate" value="60" sourceName="{text}" />'
            '</HealthData>'
        ).encode()
    vault = MagicMock()
    handler = FileUploadHandler(vault)
    result = handler.handle_upload_bytes(content, f"../../synthetic.{extension}")
    assert result["success"]
    args, kwargs = vault.store_data.call_args
    assert kwargs == {"encrypt": True}
    stored = args[1]
    assert text in stored["text_content"]
    assert base64.b64decode(stored["raw_content_b64"]) == content
    assert stored["metadata"]["original_filename"] == f"synthetic.{extension}"
    assert stored["metadata"]["file_size"] == len(content)


@pytest.mark.parametrize("filename,content", [
    ("bad.pdf", b"not a PDF"),
    ("empty.pdf", b""),
    ("bad.docx", b"not a zip document"),
    ("bad.xml", b"<HealthData><Record></HealthData>"),
    ("bad.csv", b'provider,amount\n"unterminated,42\n'),
    ("bad.csv", b"provider,amount\nextra,columns,42\n"),
    ("bad.csv", b"provider\n\xff\n"),
    ("empty.csv", b""),
    ("bad.txt", b"\xff"),
])
def test_malformed_input_raises_before_storage(filename, content):
    vault = MagicMock()
    handler = FileUploadHandler(vault)
    with pytest.raises(IngestionError):
        handler.preview_bytes(content, filename)
    with pytest.raises(IngestionError):
        handler.handle_upload_bytes(content, filename)
    vault.store_data.assert_not_called()


@pytest.mark.parametrize("filename", ["..", "", "directory/", "bad\x00.txt", "bad.exe"])
def test_invalid_names_fail_closed_without_reading_paths(filename):
    vault = MagicMock()
    with pytest.raises(IngestionError):
        FileUploadHandler(vault).handle_upload_bytes(b"synthetic", filename)
    vault.store_data.assert_not_called()


def test_upload_filename_cannot_read_an_existing_local_file(tmp_path):
    local = tmp_path / "unrelated.txt"
    local.write_bytes(b"synthetic unrelated local data")
    content = b"synthetic uploaded bytes"
    vault = MagicMock()
    handler = FileUploadHandler(vault)
    assert handler.preview_bytes(content, str(local)) == content.decode()
    handler.handle_upload_bytes(content, str(local))
    stored = vault.store_data.call_args.args[1]
    assert base64.b64decode(stored["raw_content_b64"]) == content
    assert stored["text_content"] == content.decode()
    assert local.read_bytes() == b"synthetic unrelated local data"


def test_pdf_document_is_closed_if_markdown_extraction_fails(monkeypatch):
    with pdf_parser.pymupdf.open() as doc:
        doc.new_page()
        content = doc.tobytes()
    opened = []

    def fail(document):
        opened.append(document)
        raise RuntimeError("synthetic markdown failure")

    monkeypatch.setattr(pdf_parser.pymupdf4llm, "to_markdown", fail)
    vault = MagicMock()
    with pytest.raises(IngestionError, match="synthetic markdown failure"):
        FileUploadHandler(vault).handle_upload_bytes(content, "synthetic.pdf")
    assert len(opened) == 1
    assert opened[0].is_closed
    vault.store_data.assert_not_called()


def test_cli_local_file_upload_remains_supported(tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_bytes(b"provider,amount\nSynthetic,42\n")
    vault = MagicMock()
    handler = FileUploadHandler(vault)
    assert "provider=Synthetic" in handler.preview_text(str(path))
    result = handler.handle_upload(str(path), {"category": "checking"})
    assert result["success"]
    stored = vault.store_data.call_args.args[1]
    assert "provider=Synthetic" in stored["text_content"]
    assert base64.b64decode(stored["raw_content_b64"]) == path.read_bytes()
    assert stored["metadata"]["category"] == "checking"


def test_cli_malformed_csv_fails_closed(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_bytes(b'header\n"unterminated')
    vault = MagicMock()
    handler = FileUploadHandler(vault)
    with pytest.raises(IngestionError):
        handler.preview_text(str(path))
    with pytest.raises(IngestionError):
        handler.handle_upload(str(path))
    vault.store_data.assert_not_called()
