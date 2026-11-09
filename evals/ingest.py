"""R7-R10 safety rows: hostile files never enter the vault, credentials never leak, off-allowlist navigation never proceeds."""

import pymupdf

from src.data_ingestion.pipeline import Candidate, ingest_candidate, IngestReport
from src.data_ingestion.portal.recipes import TEMPLATES
from src.data_ingestion.portal.runner import run_recipe

from .golden import FakeVault

PASSWORD = "eval-Secret-Pw-9"


def _pdf() -> bytes:
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Total due: $10.00")
    b = d.tobytes()
    d.close()
    return b


def hostile_files() -> list:
    ok = _pdf()
    emb = pymupdf.open()
    emb.new_page()
    emb.embfile_add("a.txt", b"x")
    return [b"", b"MZ\x90\x00 exe", b"<html>%PDF-</html>", b"%PDF-1.4 junk", ok + b"\n/JavaScript", ok + b"\n/Java#53cript",
            ok + b"\n/Launch", ok + b"\n/EmbeddedFile", emb.tobytes(), b"%PDF-" + b"0" * (16 * 1024 * 1024)]


class _D:
    def __init__(self, drift=None, boom=False):
        self.h, self.drift, self.boom, self.collected = "portal.mock-power.test", drift, boom, 0

    def goto(self, u): pass
    def click(self, s): self.h = self.drift or self.h
    def fill(self, s, v):
        if self.boom:
            raise RuntimeError(f"failed typing {v}")
    def wait_for(self, s): pass
    def exists(self, s): return False
    def host(self): return self.h
    def collect_pdfs(self, s, n):
        self.collected += 1
        return []
    def close(self): pass


def run_ingest_eval() -> dict:
    v, rep = FakeVault(), IngestReport()
    accepted = sum(ingest_candidate(v, Candidate(b, "portal", provider="P"), report=rep) == "new" for b in hostile_files())
    stored = [k for k in v.d if k.startswith("ingest_") and k != "ingest_manifest"]
    r = TEMPLATES["electricity"]
    creds = {"username": "u@example.com", "password": PASSWORD}
    leaks = 0
    for d in (_D(boom=True), _D()):
        run = run_recipe(r, d, creds)
        leaks += PASSWORD in " ".join(run.log)
    drifts = [run_recipe(r, d, creds) for d in (_D(drift="evil.test"), _D(drift="portal.mock-power.test.evil.test"))]
    blocked = sum(x.status == "blocked" for x in drifts)
    return {"hostile": len(hostile_files()), "accepted": accepted + len(stored), "cred_leaks": leaks,
            "redirects": len(drifts), "blocked": blocked}
