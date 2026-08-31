"""Unit tests for src.interface.upload (no Streamlit dependency needed).

Only `_try_structured_extraction` is tested directly - the rest of
upload.py is Streamlit UI wiring (`st.file_uploader`, `st.session_state`)
that needs the `streamlit.testing.v1.AppTest` harness to exercise properly
(see AGENTS.md's "Testing the UI without a browser" section), which is out
of scope for this focused regression test on the extraction-cache reuse
logic added alongside upload-form pre-fill.
"""

import os

from src.data_extraction.schemas import ExtractedDocument
from src.data_vault.vault import DataVault
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
