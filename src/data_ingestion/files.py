"""Untrusted-file gate (S-R1): bytes in, sha256 out, or UnsafeFile. Never parses beyond structure checks, never touches disk."""

import hashlib
import re
from typing import Tuple

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES = 60
MAX_XREFS = 20000
# Active-content markers; names may be #XX-obfuscated, so decode before matching.
_ACTIVE = re.compile(r"/(JavaScript|JS|Launch|EmbeddedFile|EmbeddedFiles|RichMedia|SubmitForm|ImportData)\b")
_HEX = re.compile(r"#([0-9A-Fa-f]{2})")


class UnsafeFile(ValueError):
    pass


def _norm(s: str) -> str:
    return _HEX.sub(lambda m: chr(int(m.group(1), 16)), s)


def validate_pdf(data: bytes, max_bytes: int = MAX_BYTES, max_pages: int = MAX_PAGES) -> str:
    """Return sha256 hex of a structurally safe PDF; raise UnsafeFile otherwise (fail closed)."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise UnsafeFile("empty")
    if len(data) > max_bytes:
        raise UnsafeFile("too large")
    if not data.startswith(b"%PDF-"):
        raise UnsafeFile("not a PDF (magic bytes)")
    if _ACTIVE.search(_norm(data.decode("latin-1"))):
        raise UnsafeFile("active content")
    try:
        import pymupdf

        doc = pymupdf.open(stream=bytes(data), filetype="pdf")
    except Exception:
        raise UnsafeFile("unreadable PDF") from None
    try:
        if doc.needs_pass or doc.page_count < 1 or doc.page_count > max_pages:
            raise UnsafeFile("encrypted or page count out of range")
        if doc.embfile_count():
            raise UnsafeFile("embedded files")
        # Object streams hide dictionaries from the raw scan: check decoded objects too.
        for x in range(1, min(doc.xref_length(), MAX_XREFS)):
            if _ACTIVE.search(_norm(doc.xref_object(x, compressed=False) or "")):
                raise UnsafeFile("active content")
    except UnsafeFile:
        raise
    except Exception:
        raise UnsafeFile("unreadable PDF") from None
    finally:
        doc.close()
    return hashlib.sha256(bytes(data)).hexdigest()


def pdf_text(data: bytes) -> str:
    """In-memory markdown extraction of an already-validated PDF (no temp file)."""
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(stream=bytes(data), filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc).strip()
    finally:
        doc.close()
