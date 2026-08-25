"""Upload page: auto-detected category, a confirmable metadata form, and
best-effort structured extraction - the document-datastore pipeline.

Flow: pick a file -> classify it (heuristics, LLM fallback) -> user
confirms/overrides the category and fills in a few category-specific
fields -> upload + encrypt -> best-effort LLM extraction of structured
line items for fast, accurate aggregation queries later.
"""

import os
import tempfile
from typing import Any, Callable, Dict, Optional

import streamlit as st

from src.data_extraction import category_keys, classify_document, get_category
from src.data_extraction.extractor import ExtractionError, extract_structured_data
from src.data_ingestion.handlers import FileUploadHandler, IngestionError
from src.data_vault import DataVault

_SESSION_KEYS = (
    "upload_file_signature",
    "upload_tmp_path",
    "upload_text_preview",
    "upload_detected_category",
)


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
    `on_uploaded(storage_key, data)`, if given, is called after a
    successful upload (including any structured extraction) so the caller
    can index the new document into RAG immediately.
    """
    st.header("📂 Upload Data")
    st.caption("Encrypted file upload - data will be processed locally")

    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["txt", "pdf", "csv", "json", "md"],
        label_visibility="collapsed",
    )

    if uploaded_file is None:
        _clear_upload_session_state()
        return

    file_signature = (uploaded_file.name, uploaded_file.size)
    if st.session_state.get("upload_file_signature") != file_signature:
        _prepare_upload(
            uploaded_file, file_signature, vault, encryption_key, ollama_client
        )

    if "upload_tmp_path" not in st.session_state:
        return  # preparation failed; error already shown

    st.success(f"📄 {uploaded_file.name} ({uploaded_file.size} bytes) ready for upload")

    detected_category = st.session_state["upload_detected_category"]
    keys = category_keys()
    default_index = (
        keys.index(detected_category)
        if detected_category in keys
        else keys.index("other")
    )
    st.info(
        f"Detected category: **{get_category(detected_category).label}** - "
        "confirm or change below."
    )

    with st.form("upload_metadata_form"):
        selected_key = st.selectbox(
            "Category",
            options=keys,
            index=default_index,
            format_func=lambda k: get_category(k).label,
        )
        category = get_category(selected_key)
        field_values = {
            field.key: st.text_input(field.label) for field in category.fields
        }
        submitted = st.form_submit_button("🔒 Upload & Encrypt")

    if not submitted:
        return

    tmp_path = st.session_state["upload_tmp_path"]
    text_preview = st.session_state["upload_text_preview"]
    try:
        metadata = {"category": selected_key, **field_values}
        handler = FileUploadHandler(vault, encryption_key)
        result = handler.handle_upload(tmp_path, metadata=metadata)

        if result["success"]:
            st.success("✅ File uploaded successfully!")
            st.json(
                {
                    "Storage Key": result["storage_key"],
                    "File Type": result["file_type"],
                    "Text Length": result["text_length"],
                    "Category": get_category(selected_key).label,
                }
            )

            _try_structured_extraction(
                vault, result["storage_key"], text_preview, selected_key, ollama_client
            )

            if on_uploaded:
                final_data = vault.retrieve_data(result["storage_key"])
                on_uploaded(result["storage_key"], final_data)
        else:
            st.error("❌ Upload failed")
    except IngestionError as e:
        st.error(f"❌ Upload failed: {e}")
    finally:
        _clear_upload_session_state()


def _prepare_upload(
    uploaded_file,
    file_signature,
    vault: DataVault,
    encryption_key: Optional[bytes],
    ollama_client: Optional[Any],
) -> None:
    """Write to a temp file and classify it - runs once per selected file."""
    _clear_upload_session_state()

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
        st.error(f"❌ Could not read file: {e}")
        os.unlink(tmp_path)
        return

    st.session_state["upload_file_signature"] = file_signature
    st.session_state["upload_tmp_path"] = tmp_path
    st.session_state["upload_text_preview"] = text_preview
    st.session_state["upload_detected_category"] = detected_category


def _try_structured_extraction(
    vault: DataVault,
    storage_key: str,
    text_preview: str,
    category: str,
    ollama_client: Optional[Any],
) -> None:
    """Best-effort structured extraction - failure here doesn't fail the upload."""
    if ollama_client is None or category == "other":
        return

    try:
        extraction = extract_structured_data(text_preview, category, ollama_client)
    except ExtractionError as e:
        st.info(
            f"Uploaded, but couldn't auto-extract structured data ({e}). "
            "Still searchable via chat."
        )
        return

    if extraction is None:
        st.info(
            "Uploaded, but couldn't auto-extract structured line items. "
            "Still searchable via chat."
        )
        return

    data = vault.retrieve_data(storage_key)
    data["extraction"] = extraction.model_dump()
    vault.store_data(storage_key, data)
    st.success(
        f"✅ Extracted {len(extraction.line_items)} line item(s) for fast, accurate queries"
    )


def _clear_upload_session_state() -> None:
    tmp_path = st.session_state.pop("upload_tmp_path", None)
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    for key in _SESSION_KEYS:
        st.session_state.pop(key, None)
