"""Offline upload flow, memory-only staging and extraction-cache regressions."""

import base64
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.data_extraction.extractor import ExtractionError
from src.data_extraction.schemas import ExtractedDocument
from src.data_ingestion.handlers import FileUploadHandler, IngestionError
from src.data_vault.vault import DataVault
from src.interface import upload
from src.interface.upload import _try_structured_extraction


class RaisingOllamaClient:
    def chat(self, messages, stream=False, format=None):
        raise AssertionError(
            "should not call the LLM - cached extraction should be reused"
        )


def make_vault(tmp_path) -> DataVault:
    return DataVault(vault_path=str(tmp_path / "vault"), encryption_key=os.urandom(32))


def test_reuses_cached_extraction_when_category_unchanged(tmp_path):
    vault = make_vault(tmp_path)
    vault.store_data("bill1", {"metadata": {"category": "credit_card"}})
    cached = ExtractedDocument(
        provider="Chase",
        account_identifier="4412",
        period_start="2026-06-01",
        period_end="2026-06-30",
        total=100.0,
        line_items=[{"label": "Groceries", "amount": 100.0}],
    )

    warning = _try_structured_extraction(
        vault,
        "bill1",
        "irrelevant text - the cache should be used, not re-extracted",
        "credit_card",
        RaisingOllamaClient(),
        cached_extraction=cached,
        cached_extraction_category="credit_card",
    )

    assert warning is None
    stored = vault.retrieve_data("bill1")
    assert stored["extraction"]["provider"] == "Chase"
    assert stored["extraction"]["account_identifier"] == "4412"


def test_recomputes_when_category_was_changed_after_detection(tmp_path):
    vault = make_vault(tmp_path)
    vault.store_data("bill1", {"metadata": {"category": "checking"}})
    stale_cached = ExtractedDocument(provider="Wrong category's guess")

    class FakeOllamaClient:
        def chat(self, messages, stream=False, format=None):
            return {
                "message": {
                    "content": '{"provider": "Chase", "total": 50.0, "line_items": []}'
                }
            }

    warning = _try_structured_extraction(
        vault,
        "bill1",
        "some checking statement text",
        "checking",  # user overrode the originally-detected category
        FakeOllamaClient(),
        cached_extraction=stale_cached,
        cached_extraction_category="credit_card",  # stale - detected before override
    )

    assert warning is None
    stored = vault.retrieve_data("bill1")
    assert stored["extraction"]["provider"] == "Chase"


def test_skips_extraction_for_other_category_even_with_cache(tmp_path):
    vault = make_vault(tmp_path)
    vault.store_data("bill1", {"metadata": {"category": "other"}})
    cached = ExtractedDocument(provider="Should not be stored")

    warning = _try_structured_extraction(
        vault,
        "bill1",
        "some text",
        "other",
        RaisingOllamaClient(),
        cached_extraction=cached,
        cached_extraction_category="other",
    )

    assert warning is None
    stored = vault.retrieve_data("bill1")
    assert "extraction" not in stored


@pytest.fixture
def memory_upload(monkeypatch):
    ui = MagicMock()
    ui.session_state = {}
    ui.button.return_value = False
    ui.columns.return_value = [MagicMock(), MagicMock()]
    ui.selectbox.return_value = "other"
    monkeypatch.setattr(upload, "st", ui)

    def no_staging(*args, **kwargs):
        pytest.fail("Uploads must not stage plaintext on disk")

    for name in ("NamedTemporaryFile", "TemporaryFile", "TemporaryDirectory"):
        monkeypatch.setattr(tempfile, name, no_staging)
    return ui


def uploaded_file(name="synthetic.txt", content=b"SYNTHETIC-PRIVATE-UPLOAD-76329"):
    return SimpleNamespace(name=name, size=len(content), getvalue=lambda: content)


def test_picker_accepts_existing_handler_supported_formats(memory_upload):
    memory_upload.file_uploader.return_value = []
    upload.render_upload_page(MagicMock(), None, None)
    extensions = memory_upload.file_uploader.call_args.kwargs["type"]
    assert {f".{extension}" for extension in extensions} == set(
        FileUploadHandler.SUPPORTED_FORMATS
    )


@pytest.mark.parametrize("filename", [
    "statement.txt", "../../statement.txt", r"..\..\statement.txt",
    "/untrusted/path/statement.txt",
])
def test_prepare_rerun_confirm_stays_memory_only(
    filename, tmp_path, monkeypatch, memory_upload
):
    vault = make_vault(tmp_path)
    file = uploaded_file(filename)
    memory_upload.file_uploader.return_value = [file]
    classifier = MagicMock(return_value="credit_card")
    extractor = MagicMock(return_value=ExtractedDocument(provider="Synthetic Bank"))
    monkeypatch.setattr(upload, "classify_document", classifier)
    monkeypatch.setattr(upload, "extract_structured_data", extractor)
    memory_upload.selectbox.return_value = "credit_card"
    memory_upload.text_input.return_value = ""
    client = object()
    indexed = MagicMock()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    upload.render_upload_page(vault, None, client, indexed)
    pending = memory_upload.session_state[upload._PENDING_KEY]
    assert len(pending) == 1
    assert "tmp_path" not in pending[0]
    assert "temp_path" not in pending[0]
    assert pending[0]["file_content"] == file.getvalue()
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert vault.list_keys() == ["vault_metadata"]

    upload.render_upload_page(vault, None, client, indexed)
    classifier.assert_called_once()
    extractor.assert_called_once()
    memory_upload.button.return_value = True
    upload.render_upload_page(vault, None, client, indexed)

    assert upload._PENDING_KEY not in memory_upload.session_state
    memory_upload.rerun.assert_called_once()
    extractor.assert_called_once()
    key, = [key for key in vault.list_keys() if key != "vault_metadata"]
    assert "/" not in key and "\\" not in key
    stored = vault.retrieve_data(key)
    assert base64.b64decode(stored["raw_content_b64"]) == file.getvalue()
    assert stored["text_content"] == file.getvalue().decode()
    assert stored["metadata"]["original_filename"] == "statement.txt"
    assert stored["metadata"]["category"] == "credit_card"
    assert stored["extraction"]["provider"] == "Synthetic Bank"
    indexed.assert_called_once_with(key, stored)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            persisted = path.read_bytes()
            assert file.getvalue() not in persisted
            assert base64.b64encode(file.getvalue()) not in persisted


@pytest.mark.parametrize("stage", ["parser", "classifier", "extraction", "unexpected_extraction"])
def test_prepare_failures_never_leave_staged_files(
    stage, tmp_path, monkeypatch, memory_upload
):
    vault = make_vault(tmp_path)
    file = uploaded_file()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setattr(upload, "classify_document", lambda *args: "credit_card")

    def fail(*args, **kwargs):
        if stage == "parser":
            raise IngestionError("synthetic parser failure")
        if stage == "extraction":
            raise ExtractionError("synthetic extraction failure")
        raise RuntimeError("synthetic unexpected failure")

    if stage == "parser":
        monkeypatch.setattr(FileUploadHandler, "preview_bytes", fail)
    elif stage == "classifier":
        monkeypatch.setattr(upload, "classify_document", fail)
    else:
        monkeypatch.setattr(upload, "extract_structured_data", fail)

    if stage in {"classifier", "unexpected_extraction"}:
        with pytest.raises(RuntimeError, match="synthetic unexpected failure"):
            upload._sync_pending([file], vault, None, object())
    else:
        upload._sync_pending([file], vault, None, object())
    pending = memory_upload.session_state[upload._PENDING_KEY]
    if stage == "extraction":
        assert len(pending) == 1
        assert pending[0]["extraction"] is None
        assert "tmp_path" not in pending[0]
    else:
        assert pending == []
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert vault.list_keys() == ["vault_metadata"]


def test_picker_removal_and_abandonment_drop_pending_references(
    tmp_path, monkeypatch, memory_upload
):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(upload, "classify_document", lambda *args: "other")
    files = [uploaded_file(), uploaded_file("second.txt", b"synthetic second file")]
    upload._sync_pending(files, vault, None, None)
    assert len(memory_upload.session_state[upload._PENDING_KEY]) == 2
    upload._sync_pending(files[1:], vault, None, None)
    assert len(memory_upload.session_state[upload._PENDING_KEY]) == 1
    assert memory_upload.session_state[upload._PENDING_KEY][0]["filename"] == "second.txt"
    memory_upload.file_uploader.return_value = []
    upload.render_upload_page(vault, None, None)
    assert upload._PENDING_KEY not in memory_upload.session_state
    assert vault.list_keys() == ["vault_metadata"]


def test_confirm_parser_failure_stores_nothing(tmp_path, monkeypatch, memory_upload):
    vault = make_vault(tmp_path)
    file = uploaded_file()
    monkeypatch.setattr(upload, "classify_document", lambda *args: "other")
    memory_upload.file_uploader.return_value = [file]
    upload.render_upload_page(vault, None, None)

    def fail(*args):
        raise IngestionError("synthetic confirm parser failure")

    monkeypatch.setattr(FileUploadHandler, "preview_bytes", fail)
    memory_upload.button.return_value = True
    upload.render_upload_page(vault, None, None)
    assert vault.list_keys() == ["vault_metadata"]
    assert upload._PENDING_KEY not in memory_upload.session_state
    assert any("synthetic confirm parser failure" in str(call)
               for call in memory_upload.write.call_args_list)
