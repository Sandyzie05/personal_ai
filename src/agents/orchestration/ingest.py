"""Untrusted-source ingestion: Gmail (read-only MCP) and utility portals, both via quarantine.

Raw text never leaves `extract_facts`; Findings are built from schema fields only and are
tagged trust="untrusted", so they can inform a summary but cannot drive writes unattended.
"""

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Protocol

from src.agents import store
from .quarantine import extract_facts
from .schemas import EmailFacts, Finding, PlanStep, StatementFacts

READ_ONLY_TOOL = re.compile(r"^(search|get|list|read)_[a-z_]*$")
MAX_ITEMS = 50


class ReadOnlyMcp:
    """Allowlisted read-only view over an McpManager server (Gmail)."""

    def __init__(self, mcp: Any, server_id: str):
        self.mcp, self.server_id = mcp, server_id

    def call(self, tool: str, args: Dict[str, Any]) -> str:
        if not READ_ONLY_TOOL.match(tool) or self.mcp.is_sensitive_tool(tool):
            raise PermissionError(f"tool '{tool}' is not read-only")
        result = self.mcp.call_tool(self.server_id, tool, args)
        if not result.ok:
            raise ConnectionError(result.text[:100])
        return result.text


def gmail_fetcher(ro: ReadOnlyMcp, search_tool: str = "search_threads", query: str = "newer_than:1d") -> Callable[[], List[dict]]:
    def fetch() -> List[dict]:
        text = ro.call(search_tool, {"query": query})
        try:
            data = json.loads(text)
            items = data if isinstance(data, list) else data.get("threads", data.get("messages", []))
            return [i for i in items if isinstance(i, dict)]
        except (ValueError, AttributeError):
            return [{"id": "0", "body": text}]
    return fetch


TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")


def holdings_fetcher(ro: ReadOnlyMcp, tool: str = "get_positions") -> Callable[[], List[dict]]:
    """Brokerage read-only holdings. Only ticker-shaped symbols survive parsing: quantities, prices, account
    ids and any free text from the broker are dropped here (minimal disclosure + no injection surface)."""
    def fetch() -> List[dict]:
        try:
            data = json.loads(ro.call(tool, {}))
        except ValueError:
            return []
        items = data if isinstance(data, list) else (data.get("positions") or data.get("holdings") or []) if isinstance(data, dict) else []
        syms = (str(i.get("symbol", "")).upper() if isinstance(i, dict) else str(i).upper() for i in items)
        return [{"symbol": x} for x in dict.fromkeys(syms) if TICKER.match(x)][:25]
    return fetch


def brokerage_worker(fetch: Callable[[], List[dict]]):
    """Pool thread, no vault. Findings carry only validated tickers; trust stays 'untrusted' (external service)."""
    def run(step: PlanStep, inputs) -> List[Finding]:
        syms = [h["symbol"] for h in fetch() if TICKER.match(str(h.get("symbol", "")))]
        if not syms:
            return [Finding(agent="brokerage", claim_key="holdings", summary="No holdings returned.", confidence=0,
                            status="unavailable", trust="untrusted")]
        return [Finding(agent="brokerage", claim_key="holdings", value=",".join(syms),
                        summary=f"{len(syms)} holdings read (read-only)", evidence_ids=("brokerage:holdings",),
                        confidence=0.8, trust="untrusted")]
    return run


def _flag(vault: Any, source: str) -> None:
    if vault is not None:
        store.log_activity(vault, {"kind": "injection_flagged", "source": source})


def digest_emails(emails: Iterable[dict], client: Any, vault: Any = None) -> List[Finding]:
    out: List[Finding] = []
    for i, e in enumerate(list(emails)[:MAX_ITEMS]):
        eid = re.sub(r"[^A-Za-z0-9_-]", "", str(e.get("id", i)))[:24] or str(i)
        raw = f"From: {e.get('from', '')}\nSubject: {e.get('subject', '')}\n\n{e.get('body', '')}"
        ex = extract_facts(raw, f"gmail:{eid}", EmailFacts, client)
        if ex.flagged:
            _flag(vault, f"gmail:{eid}")
        if ex.facts is None:
            out.append(Finding(agent="email", claim_key=f"email:{eid}", summary="Could not safely extract; ignored.",
                               confidence=0, status="uncertain", trust="untrusted"))
            continue
        f = ex.facts
        out.append(Finding(agent="email", claim_key=f"email:{eid}", value=f.amount,
                           summary=f"{f.category} from {f.sender_domain}" + (f" due {f.due_date}" if f.due_date else "")
                           + (f" ({f.topic})" if f.topic else "") + (" [suspicious content ignored]" if ex.flagged else ""),
                           evidence_ids=(f"gmail:{eid}",), confidence=0.7, trust="untrusted"))
    return out


class PortalFetcher(Protocol):
    def fetch(self, provider: str) -> List[dict]:  # [{"id": ..., "text": ...}]
        ...


def ingest_statements(fetcher: PortalFetcher, provider: str, client: Any, vault: Any = None) -> List[Finding]:
    out: List[Finding] = []
    for doc in fetcher.fetch(provider)[:MAX_ITEMS]:
        ex = extract_facts(str(doc.get("text", "")), f"portal:{provider}", StatementFacts, client)
        if ex.flagged:
            _flag(vault, f"portal:{provider}")
        if ex.facts is None:
            out.append(Finding(agent="portal", claim_key=f"statement:{provider[:30]}", summary="Unparseable portal content; ignored.",
                               confidence=0, status="uncertain", trust="untrusted"))
            continue
        s = ex.facts
        out.append(Finding(agent="portal", claim_key=f"statement:{s.provider[:30]}:{s.period_end or 'unknown'}",
                           value=s.total_due, summary=f"{s.provider} statement", evidence_ids=(f"portal:{provider[:30]}",),
                           confidence=0.7, trust="untrusted"))
    return out


def email_worker(fetch: Callable[[], List[dict]], client: Any):
    """Runs in a pool thread: never touches the vault (sqlite is single-threaded). Injection audit entries are
    written by service.run_query on the calling thread from the findings' '[suspicious content ignored]' marker."""
    def run(step: PlanStep, inputs) -> List[Finding]:
        return digest_emails(fetch(), client, None)
    return run


def portal_worker(fetcher: PortalFetcher, providers: Iterable[str], client: Any):
    def run(step: PlanStep, inputs) -> List[Finding]:
        return [f for p in providers for f in ingest_statements(fetcher, p, client, None)]
    return run
