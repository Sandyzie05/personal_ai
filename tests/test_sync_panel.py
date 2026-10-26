"""Upload page renders the Sync panel (AppTest, throwaway HOME) with ledger + overdue badge, no network."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.data_ingestion.pipeline import set_expectation
from src.data_vault import DataVault
from src.security.auth import AuthManager

MAIN = str(Path(__file__).parent.parent / "src" / "interface" / "main.py")


def test_upload_page_shows_sync_panel_and_overdue(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    dek = AuthManager().register("smoke-test-password")
    seed = DataVault(vault_path=str(home / ".personal_ai_vault"), encryption_key=dek)
    set_expectation(seed, "Mock Power", "monthly")
    del seed
    at = AppTest.from_file(MAIN, default_timeout=120)
    at.session_state["vault_key"] = dek
    at.run()
    at.radio[0].set_value("upload").run()
    assert not at.exception
    assert any("Sync" in s.value for s in at.subheader)
    assert any("Mock Power" in w.value for w in at.warning)
