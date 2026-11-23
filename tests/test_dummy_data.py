"""dummy_data/ PDFs are valid, classify to their category, and seed an isolated vault."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import seed_dummy_vault  # noqa: E402
from src.ai_engine.chat_engine import ChatEngine  # noqa: E402
from src.data_extraction import classify_document  # noqa: E402
from src.data_ingestion.files import pdf_text, validate_pdf  # noqa: E402
from src.security.auth import AuthManager  # noqa: E402

DATA = ROOT / "dummy_data"
EXTRACTIONS = json.loads((DATA / "extractions.json").read_text())


@pytest.mark.parametrize("name", sorted(EXTRACTIONS))
def test_pdf_valid_classified_and_consistent(name):
    ex = EXTRACTIONS[name]
    data = (DATA / name).read_bytes()
    validate_pdf(data)
    text = pdf_text(data)
    assert classify_document(text, None) == ex["category"]
    assert round(sum(i["amount"] for i in ex["line_items"]), 2) == ex["total"]
    assert f"{ex['total']:,.2f}" in text
    assert "SYNTHETIC DATA" in text


def test_every_category_covered():
    cats = {e["category"] for e in EXTRACTIONS.values()}
    assert cats == {
        "electricity",
        "gas",
        "mobile",
        "credit_card",
        "checking",
        "brokerage",
    }


def test_seed_refuses_real_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(SystemExit):
        seed_dummy_vault.seed()
    assert not (tmp_path / ".personal_ai_auth").exists()


def test_seed_isolated_idempotent_and_queryable(tmp_path, monkeypatch):
    home = tmp_path / ".dummy_home"
    monkeypatch.setenv("HOME", str(home))
    assert seed_dummy_vault.seed() == len(EXTRACTIONS)
    assert seed_dummy_vault.seed() == 0
    key = AuthManager(str(home / ".personal_ai_auth")).authenticate(
        seed_dummy_vault.PASSWORD
    )
    engine = ChatEngine(vault_path=str(home / ".personal_ai_vault"), encryption_key=key)
    gas = engine.get_structured_records(
        category="gas", start_date="2025-09-01", end_date="2025-09-30"
    )
    assert [r["extraction"]["total"] for r in gas] == [50.80]
