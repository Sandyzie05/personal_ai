"""Idempotent, bounded bill ingestion (R7): fetchers -> validate -> encrypt+store -> extract -> index -> verify -> report.

Manifest and expectations are internal vault keys (encrypted, never indexed). Pure vault/callback logic: no network here.
"""

import base64
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from .files import UnsafeFile, pdf_text, validate_pdf

MANIFEST_KEY = "ingest_manifest"
EXPECT_KEY = "ingest_expectations"
MAX_FILES_PER_RUN = 20
MAX_RUN_SECONDS = 300
CADENCE_DAYS = {"monthly": 31, "quarterly": 92}


@dataclass
class Candidate:
    data: bytes
    source: str  # gmail | portal | manual
    source_ref: str = ""
    provider: str = ""
    category: str = ""
    period_start: Optional[str] = None  # ISO date
    period_end: Optional[str] = None


@dataclass
class IngestReport:
    new: List[str] = field(default_factory=list)
    duplicates: int = 0
    superseded: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)  # reasons only, never content
    needs_you: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.new)} new" + (f" ({', '.join(self.new)})" if self.new else "")]
        if self.duplicates:
            parts.append(f"{self.duplicates} duplicate skipped")
        if self.superseded:
            parts.append(f"{len(self.superseded)} corrected bill(s) superseded an older copy")
        if self.failed:
            parts.append(f"{len(self.failed)} failed ({'; '.join(self.failed)})")
        if self.needs_you:
            parts.append(f"{len(self.needs_you)} need you ({'; '.join(self.needs_you)})")
        return ", ".join(parts)


def _manifest(vault) -> Dict[str, Dict[str, Any]]:
    return (vault.retrieve_data(MANIFEST_KEY) or {}).get("docs", {})


def _save(vault, docs) -> None:
    vault.store_data(MANIFEST_KEY, {"docs": docs}, encrypt=True)


def ingest_candidate(
    vault,
    c: Candidate,
    on_indexed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_superseded: Optional[Callable[[str], None]] = None,
    verify: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    report: Optional[IngestReport] = None,
) -> str:
    """-> 'new' | 'duplicate' | 'failed'. Stores encrypted BEFORE parsing; parse failure leaves status=failed (never half-indexed)."""
    r = report if report is not None else IngestReport()
    label = f"{c.provider or 'unknown'} {c.period_end or ''}".strip()
    try:
        sha = validate_pdf(c.data)
    except UnsafeFile as e:
        r.failed.append(f"{label}: rejected ({e})")
        return "failed"
    docs = _manifest(vault)
    if sha in docs and docs[sha]["status"] in ("indexed", "superseded", "new"):
        r.duplicates += 1
        return "duplicate"
    key = f"ingest_{sha[:16]}"
    meta = {
        "original_filename": f"{sha[:16]}.pdf",  # never the remote filename
        "file_type": "pdf", "file_size": len(c.data), "content_type": "text",
        "upload_timestamp": datetime.now().isoformat(), "source": c.source,
        **{k: v for k, v in (("provider", c.provider), ("category", c.category),
                              ("period_start", c.period_start), ("period_end", c.period_end)) if v},
    }
    vault.store_data(key, {"metadata": meta, "text_content": "",
                           "raw_content_b64": base64.b64encode(c.data).decode()}, encrypt=True)
    rec = {"sha256": sha, "source": c.source, "source_ref": c.source_ref[:80], "provider": c.provider,
           "category": c.category, "period_start": c.period_start, "period_end": c.period_end,
           "fetched_at": datetime.now().isoformat(), "status": "new", "storage_key": key}
    try:
        text = pdf_text(c.data)
        if not text:
            raise ValueError("no text layer")
        data = {"metadata": meta, "text_content": text, "raw_content_b64": base64.b64encode(c.data).decode()}
        vault.store_data(key, data, encrypt=True)
        if verify is not None and not verify(key, data):
            raise ValueError("verification failed")
        if on_indexed:
            on_indexed(key, data)
        rec["status"] = "indexed"
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = str(e)[:80]
        docs[sha] = rec
        _save(vault, docs)
        r.failed.append(f"{label}: {rec['error']}")
        return "failed"
    if c.provider and c.period_end:  # corrected bill: same provider+period, different hash
        for old_sha, old in docs.items():
            if (old_sha != sha and old["status"] == "indexed" and old.get("provider") == c.provider
                    and old.get("period_end") == c.period_end):
                old["status"] = "superseded"
                r.superseded.append(label)
                if on_superseded:
                    on_superseded(old["storage_key"])
    docs[sha] = rec
    _save(vault, docs)
    r.new.append(label)
    return "new"


def run_ingest(
    vault,
    fetchers: Iterable[Callable[[], Iterable[Candidate]]],
    on_indexed=None, on_superseded=None, verify=None,
    max_files: int = MAX_FILES_PER_RUN, max_seconds: int = MAX_RUN_SECONDS,
) -> IngestReport:
    """Headless entry point (also used by the Sync panel). A fetcher raising never aborts the others."""
    r, n, t0 = IngestReport(), 0, time.monotonic()
    for fetch in fetchers:
        try:
            for c in fetch():
                if n >= max_files or time.monotonic() - t0 > max_seconds:
                    r.needs_you.append("run cap reached; sync again")
                    return r
                n += 1
                ingest_candidate(vault, c, on_indexed, on_superseded, verify, r)
        except PermissionError as e:
            r.needs_you.append(str(e)[:80])
        except Exception as e:
            r.failed.append(f"fetcher: {type(e).__name__}")
    return r


# ---- expected-bills ledger -------------------------------------------------

def set_expectation(vault, provider: str, cadence: str) -> None:
    if cadence not in CADENCE_DAYS:
        raise ValueError("cadence must be monthly or quarterly")
    ex = vault.retrieve_data(EXPECT_KEY) or {"providers": {}}
    ex["providers"][provider] = cadence
    vault.store_data(EXPECT_KEY, ex, encrypt=True)


def overdue(vault, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Providers whose newest indexed bill is older than cadence. No network."""
    today = today or date.today()
    ex = (vault.retrieve_data(EXPECT_KEY) or {}).get("providers", {})
    latest: Dict[str, date] = {}
    for rec in _manifest(vault).values():
        if rec["status"] == "indexed" and rec.get("provider") and rec.get("period_end"):
            d = date.fromisoformat(rec["period_end"])
            latest[rec["provider"]] = max(d, latest.get(rec["provider"], d))
    out = []
    for p, cad in ex.items():
        last = latest.get(p)
        late = (today - last).days - CADENCE_DAYS[cad] if last else None
        if last is None or late > 0:
            out.append({"provider": p, "last_period_end": last.isoformat() if last else None,
                        "days_overdue": late})
    return out


def sync_status(vault) -> Dict[str, Any]:
    docs = _manifest(vault).values()
    c: Dict[str, int] = {}
    for d in docs:
        c[d["status"]] = c.get(d["status"], 0) + 1
    return {"counts": c, "overdue": overdue(vault)}
