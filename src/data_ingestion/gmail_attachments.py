"""Gmail bill-PDF attachments via the read-only MCP wrapper (R8 / S-R4).

Only sender-allowlisted messages and their PDF attachments are used; message bodies are never read into
prompts. Tool names are parameters because attachment support depends on the user's Gmail MCP server
(verify with `Agents -> MCP Servers` which tools it exposes; `get_attachment` is the assumed default).
"""

import base64
import binascii
import json
import re
from typing import Any, Callable, Dict, Iterable, List

from src.agents.orchestration.ingest import ReadOnlyMcp

from .files import MAX_BYTES
from .pipeline import Candidate

_ADDR = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", re.I)
MAX_MESSAGES = 20
MAX_ATTACHMENTS_PER_MESSAGE = 3


def sender_allowed(from_header: str, domains: Iterable[str]) -> bool:
    """Exact domain or subdomain of an allowlisted domain; display names can't spoof it (only the address is parsed)."""
    m = _ADDR.search(from_header or "")
    if not m:
        return False
    host = m.group(1).lower()
    return any(host == d or host.endswith("." + d) for d in (x.lower() for x in domains))


def gmail_attachment_fetcher(
    ro: ReadOnlyMcp,
    provider: str,
    category: str,
    sender_domains: List[str],
    search_tool: str = "search_messages",
    attachment_tool: str = "get_attachment",
    newer_than: str = "45d",
) -> Callable[[], List[Candidate]]:
    def fetch() -> List[Candidate]:
        q = "(" + " OR ".join(f"from:{d}" for d in sender_domains) + f") has:attachment filename:pdf newer_than:{newer_than}"
        try:
            data = json.loads(ro.call(search_tool, {"query": q}))
        except ValueError:
            return []
        msgs = data if isinstance(data, list) else data.get("messages", [])
        out: List[Candidate] = []
        for m in [x for x in msgs if isinstance(x, dict)][:MAX_MESSAGES]:
            if not sender_allowed(str(m.get("from", "")), sender_domains):
                continue  # query is a hint; the code-side check is the control
            mid = re.sub(r"[^A-Za-z0-9_-]", "", str(m.get("id", "")))[:40]
            atts: List[Dict[str, Any]] = [a for a in m.get("attachments", []) if isinstance(a, dict)]
            for a in atts[:MAX_ATTACHMENTS_PER_MESSAGE]:
                name = str(a.get("filename", "")).lower()
                if not (name.endswith(".pdf") or a.get("mimeType") == "application/pdf"):
                    continue
                aid = re.sub(r"[^A-Za-z0-9_=-]", "", str(a.get("id", "")))[:400]
                raw = ro.call(attachment_tool, {"message_id": mid, "attachment_id": aid})
                try:
                    blob = json.loads(raw)
                    raw = blob.get("data", "") if isinstance(blob, dict) else raw
                except ValueError:
                    pass
                if len(raw) > MAX_BYTES * 4 // 3 + 16:  # bound before decoding
                    continue
                try:
                    pdf = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
                except (binascii.Error, ValueError):
                    continue
                out.append(Candidate(pdf, "gmail", f"{mid}/{aid[:20]}", provider, category))
        return out
    return fetch
