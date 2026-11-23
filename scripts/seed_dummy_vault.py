"""Seed a throwaway vault with the synthetic PDFs in dummy_data/ (run via `make run-dummy`).

Refuses to run unless HOME is the repo's .dummy_home/, so the real ~/.personal_ai_vault and
~/.personal_ai_auth are never touched. The key comes from AuthManager like any login. Structured
fields come from dummy_data/extractions.json (ground truth), so no model is needed to seed. The
RAG index is rebuilt from the vault when the app first chats (or Chat -> Advanced -> Rebuild RAG Index).
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_extraction.schemas import ExtractedDocument  # noqa: E402
from src.data_ingestion.handlers import FileUploadHandler  # noqa: E402
from src.data_vault.vault import create_vault, is_internal_vault_key  # noqa: E402
from src.security.auth import AuthManager  # noqa: E402

DATA = ROOT / "dummy_data"
DUMMY_HOME = ROOT / ".dummy_home"
PASSWORD = os.environ.get("DUMMY_PASSWORD", "dummy-data-demo")


def seed(data_dir: Path = DATA) -> int:
    home = Path(os.path.expanduser("~")).resolve()
    if home.name != ".dummy_home":
        raise SystemExit(
            "Refusing to seed: HOME must be a .dummy_home directory (use `make run-dummy`), "
            "so your real vault is never touched."
        )
    home.mkdir(parents=True, exist_ok=True)
    auth = AuthManager()
    key = (
        auth.authenticate(PASSWORD) if auth.is_registered() else auth.register(PASSWORD)
    )
    vault = create_vault(encryption_key=key)
    have = {
        ((vault.retrieve_data(k) or {}).get("metadata") or {}).get("original_filename")
        for k in vault.list_keys()
        if not is_internal_vault_key(k)
    }
    handler = FileUploadHandler(vault, key)
    added = 0
    for name, ex in json.loads((data_dir / "extractions.json").read_text()).items():
        if name in have:
            continue
        meta = {
            "category": ex["category"],
            "provider": ex["provider"],
            "account_label": ex["account_identifier"],
            "period_start": ex["period_start"],
            "period_end": ex["period_end"],
        }
        result = handler.handle_upload_bytes(
            (data_dir / name).read_bytes(), name, metadata=meta
        )
        data = vault.retrieve_data(result["storage_key"])
        fields = {k: v for k, v in ex.items() if k != "category"}
        data["extraction"] = ExtractedDocument(**fields).model_dump()
        vault.store_data(result["storage_key"], data)
        added += 1
    return added


if __name__ == "__main__":
    n = seed()
    print(
        f"Dummy vault ready in {DUMMY_HOME} ({n} new document(s)). Log in with password: {PASSWORD}"
    )
