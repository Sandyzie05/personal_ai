"""Upload page: auto-detected category, a confirmable metadata form, and
best-effort structured extraction - the document-datastore pipeline.

Flow: pick one or more files -> each is classified individually
(heuristics, LLM fallback) -> user confirms/overrides each file's category
(and, optionally, a few category-specific fields) -> "Upload All" encrypts
+ stores every file -> best-effort LLM extraction of structured line items
per file for fast, accurate aggregation queries later.
"""

import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

import streamlit as st

from src.data_extraction import category_keys, classify_document, get_category
from src.data_extraction.extractor import ExtractionError, extract_structured_data
from src.data_ingestion.handlers import FileUploadHandler, IngestionError
from src.data_vault import DataVault

_PENDING_KEY = "upload_pending_files"

# Below this many extracted characters, a PDF almost certainly has no
# embedded text layer (i.e. it's a scanned image) - there's no OCR fallback
# yet, so warn the user rather than silently indexing an empty document.
_SCANNED_PDF_TEXT_THRESHOLD = 50


def render_upload_page(
    vault: DataVault,
    encryption_key: Optional[bytes],
    ollama_client: Optional[Any],
    on_uploaded: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> None:
    """Render the Upload page.

    `ollama_client` is used for LLM-based category classification (fallback
    only) and structured extraction; both degrade gracefully to "other"
    category / no extraction when it's None (e.g. chat engine unavailable).
    `on_uploaded(storage_key, data)`, if given, is called once per
    successfully uploaded file so the caller can index it into RAG
    immediately.
    """
    st.header("📂 Upload Data")
    st.caption(
        "Encrypted file upload - data will be processed locally. Select "
        "multiple files at once to upload a whole group (e.g. every "
        "T-Mobile bill you have) in one go."
    )

    uploaded_files = st.file_uploader(
        "Choose file(s)",
        type=["txt", "pdf", "csv", "json", "md"],
        label_visibility="collapsed",
        accept_multiple_files=True,
    )

    if not uploaded_files:
        _clear_all_pending()
        return

    _sync_pending(uploaded_files, vault, encryption_key, ollama_client)
    pending: List[Dict[str, Any]] = st.session_state.get(_PENDING_KEY, [])
    if not pending:
        return  # every file failed to prepare; errors already shown

    st.success(f"{len(pending)} file(s) ready for upload")
    keys = category_keys()

    for entry in pending:
        _render_pending_row(entry, keys)

    if st.button(
        f"🔒 Upload All ({len(pending)})", type="primary", use_container_width=True
    ):
        _upload_all(pending, vault, encryption_key, ollama_client, on_uploaded)
        _clear_all_pending()
        st.rerun()


def _render_pending_row(entry: Dict[str, Any], keys: List[str]) -> None:
    """One file's category selector (+ optional manual field overrides)."""
    signature_key = entry["signature_key"]
    category = get_category(entry["selected_category"])

    with st.container(border=True):
        col1, col2 = st.columns([3, 2])
        with col1:
            st.write(f"📄 **{entry['filename']}** ({entry['size']} bytes)")
            st.caption(
                f"Auto-detected: {get_category(entry['detected_category']).label}"
            )
        with col2:
            entry["selected_category"] = st.selectbox(
                "Category",
                options=keys,
                index=keys.index(entry["selected_category"])
                if entry["selected_category"] in keys
                else keys.index("other"),
                format_func=lambda k: get_category(k).label,
                key=f"category_{signature_key}",
                label_visibility="collapsed",
            )

        if category.fields:
            with st.expander(
                "Optional details (auto-filled from the document if left blank)"
            ):
                for field in category.fields:
                    entry["field_values"][field.key] = st.text_input(
                        field.label,
                        value=entry["field_values"].get(field.key, ""),
                        key=f"field_{field.key}_{signature_key}",
                    )


def _sync_pending(
    uploaded_files: List[Any],
    vault: DataVault,
    encryption_key: Optional[bytes],
    ollama_client: Optional[Any],
) -> None:
    """Keep `st.session_state[_PENDING_KEY]` in sync with the current file picker.

    Runs classification once per newly-added file (tracked by (name, size)
    signature) and drops pending entries for files the user has since
    removed from the picker, cleaning up their temp files.
    """
    pending: List[Dict[str, Any]] = st.session_state.setdefault(_PENDING_KEY, [])
    current_signatures = {(f.name, f.size) for f in uploaded_files}
    pending_signatures = {entry["signature"] for entry in pending}

    # Drop entries for files no longer in the picker.
    kept = []
    for entry in pending:
        if entry["signature"] in current_signatures:
            kept.append(entry)
        else:
            _cleanup_tmp(entry["tmp_path"])
    st.session_state[_PENDING_KEY] = kept
    pending = kept

    # Prepare (write temp file + classify) any newly-added files.
    for uploaded_file in uploaded_files:
        signature = (uploaded_file.name, uploaded_file.size)
        if signature in pending_signatures:
            continue
        entry = _prepare_one(uploaded_file, vault, encryption_key, ollama_client)
        if entry:
            pending.append(entry)

    st.session_state[_PENDING_KEY] = pending


def _prepare_one(
    uploaded_file,
    vault: DataVault,
    encryption_key: Optional[bytes],
    ollama_client: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """Write one uploaded file to a temp path and classify it."""
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=f"_{uploaded_file.name}"
    ) as tmp_file:
        tmp_file.write(uploaded_file.getvalue())
        tmp_path = tmp_file.name

    try:
        handler = FileUploadHandler(vault, encryption_key)
        text_preview = handler.preview_text(tmp_path)
        detected_category = classify_document(text_preview, ollama_client)
    except IngestionError as e:
        st.error(f"❌ Could not read {uploaded_file.name}: {e}")
        _cleanup_tmp(tmp_path)
        return None

    if (
        uploaded_file.name.lower().endswith(".pdf")
        and len(text_preview.strip()) < _SCANNED_PDF_TEXT_THRESHOLD
    ):
        st.warning(
            f"⚠️ **{uploaded_file.name}**: almost no text could be extracted. "
            "This usually means it's a scanned/image-only PDF with no "
            "embedded text layer - OCR isn't supported yet, so search and "
            "chat answers for this file may be empty or unreliable."
        )

    return {
        "signature": (uploaded_file.name, uploaded_file.size),
        "signature_key": f"{uploaded_file.name}_{uploaded_file.size}".replace(" ", "_"),
        "filename": uploaded_file.name,
        "size": uploaded_file.size,
        "tmp_path": tmp_path,
        "text_preview": text_preview,
        "detected_category": detected_category,
        "selected_category": detected_category,
        "field_values": {},
    }


def _upload_all(
    pending: List[Dict[str, Any]],
    vault: DataVault,
    encryption_key: Optional[bytes],
    ollama_client: Optional[Any],
    on_uploaded: Optional[Callable[[str, Dict[str, Any]], None]],
) -> None:
    """Encrypt + store every pending file, best-effort per file.

    Runs inside a `st.status` block so each file's outcome renders live as
    it's processed - Streamlit streams UI updates as the script executes,
    so this doubles as progress feedback without needing a background
    thread. Replaces the old per-file `st.success("RAG index updated...")`
    spam (one message per file) with a single running log.
    """
    handler = FileUploadHandler(vault, encryption_key)
    succeeded, failed = 0, 0
    total = len(pending)

    with st.status(f"Uploading {total} file(s)...", expanded=True) as status:
        for i, entry in enumerate(pending, start=1):
            filename = entry["filename"]
            status.update(label=f"Uploading {filename} ({i}/{total})")
            try:
                selected_category = entry["selected_category"]
                metadata = {
                    "category": selected_category,
                    **{k: v for k, v in entry["field_values"].items() if v},
                }
                result = handler.handle_upload(entry["tmp_path"], metadata=metadata)
                if not result["success"]:
                    failed += 1
                    st.write(f"❌ {filename}: upload failed")
                    continue

                consistency_warning = _try_structured_extraction(
                    vault,
                    result["storage_key"],
                    entry["text_preview"],
                    selected_category,
                    ollama_client,
                )

                if on_uploaded:
                    final_data = vault.retrieve_data(result["storage_key"])
                    on_uploaded(result["storage_key"], final_data)

                succeeded += 1
                st.write(f"✅ {filename}")
                if consistency_warning:
                    st.write(consistency_warning)
            except IngestionError as e:
                failed += 1
                st.write(f"❌ {filename}: {e}")

        final_label = f"Uploaded {succeeded}/{total} file(s)"
        if failed:
            final_label += f" - {failed} failed"
        status.update(
            label=final_label,
            state="error" if failed else "complete",
            expanded=bool(failed),
        )


def _try_structured_extraction(
    vault: DataVault,
    storage_key: str,
    text_preview: str,
    category: str,
    ollama_client: Optional[Any],
) -> Optional[str]:
    """Best-effort structured extraction - failure here doesn't fail the upload.

    Returns a warning message if the extraction looks internally
    inconsistent (see `_extraction_consistency_warning`), or None if
    extraction was skipped or looked fine.
    """
    if ollama_client is None or category == "other":
        return None

    try:
        extraction = extract_structured_data(text_preview, category, ollama_client)
    except ExtractionError:
        return None

    if extraction is None:
        return None

    data = vault.retrieve_data(storage_key)
    data["extraction"] = extraction.model_dump()
    vault.store_data(storage_key, data)

    return _extraction_consistency_warning(extraction)


def _extraction_consistency_warning(extraction) -> Optional[str]:
    """Flag when the extracted total doesn't match the sum of line items.

    The schema's own invariant is that credits/discounts are negative
    amounts, so a plain sum over line_items should equal total (see
    ExtractedDocument's docstring). A mismatch usually means the LLM
    missed, duplicated, or misread a line item from the source text - a
    concrete, cheap accuracy check that doesn't require a second model
    call.
    """
    if extraction.total is None or not extraction.line_items:
        return None
    line_item_sum = sum(item.amount for item in extraction.line_items)
    if abs(line_item_sum - extraction.total) > 0.01:
        return (
            f"⚠️ extraction check: total (${extraction.total:.2f}) doesn't "
            f"match the sum of line items (${line_item_sum:.2f}) - worth "
            "double-checking this document's extracted data."
        )
    return None


def _cleanup_tmp(tmp_path: str) -> None:
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _clear_all_pending() -> None:
    pending: List[Dict[str, Any]] = st.session_state.pop(_PENDING_KEY, [])
    for entry in pending:
        _cleanup_tmp(entry["tmp_path"])
