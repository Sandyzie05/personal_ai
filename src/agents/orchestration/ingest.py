"""Untrusted-source ingestion: Gmail (read-only MCP) and utility portals, both via quarantine.

Raw text never leaves `extract_facts`; Findings are built from schema fields only and are
tagged trust="untrusted", so they can inform a summary but cannot drive writes unattended.
"""

import difflib
import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Union

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
        from src.observability import otel

        otel.count("personal_ai.tool.calls", tool=tool, server="mcp")
        with otel.span(f"tool {tool}", tool=tool, server="mcp") as sp:
            result = self.mcp.call_tool(self.server_id, tool, args)
            sp.set(ok=result.ok)
        if not result.ok:
            raise ConnectionError(result.text[:100])
        return result.text


DIGEST_THREADS = 5  # each one is a local-model quarantine call; 10 took minutes
DEFAULT_SCOPE = {"query": "newer_than:2d", "label": "in the last 2 days"}
_DOMAIN = re.compile(r"\b((?:[a-z0-9-]{1,40}\.){1,4}(?:com|net|org|gov|edu|io|co|us|ca|uk))\b")
_TOPIC_WORDS = {"electricity": ("electric", "power"), "gas": ("gas",), "mobile": ("phone", "mobile", "wireless"),
                "credit_card": ("credit card", "card statement"), "brokerage": ("brokerage",), "checking": ("bank",)}


def digest_scope(question: str, sender_rows: List[dict], today: Optional[date] = None) -> Dict[str, str]:
    """Gmail search scope for an Email Digest question, built by code from validated pieces only: sender domains come
    from the user's bill-sender allowlist (provider name, typo-tolerant, or category word) or a domain typed in the
    question; dates are computed. No question text ever enters the Gmail query."""
    q, today = " ".join(question.lower().split()), today or date.today()
    words = re.findall(r"[a-z0-9]+", q)
    domains: List[str] = []
    for row in sender_rows:
        name = row["provider"].lower().split()
        grams = [" ".join(words[i:i + len(name)]) for i in range(len(words) - len(name) + 1)]
        named = any(difflib.SequenceMatcher(None, " ".join(name), g).ratio() >= 0.85 for g in grams)
        topic = any(w in q for w in (row["category"].replace("_", " "),) + _TOPIC_WORDS.get(row["category"], ()))
        if named or topic or row["domain"] in q:
            domains.append(row["domain"])
    domains += [d for d in _DOMAIN.findall(q) if d not in domains]
    if "this month" in q:
        start = today.replace(day=1)
        when, label = f"after:{start:%Y/%m/%d}", f"since {start.isoformat()}"
    elif "last month" in q:
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        when, label = f"after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}", f"from {start.isoformat()} to {end.isoformat()}"
    elif "today" in q:
        when, label = "newer_than:1d", "today"
    elif "week" in q:
        when, label = "newer_than:7d", "in the last 7 days"
    elif m := re.search(r"(?:last|past) (\d{1,3}) days?", q):
        n = max(1, min(int(m.group(1)), 400))
        when, label = f"newer_than:{n}d", f"in the last {n} days"
    elif domains:
        when, label = "newer_than:31d", "in the last 31 days"
    else:
        return dict(DEFAULT_SCOPE)
    if not domains:
        return {"query": when, "label": label}
    senders = " OR ".join(f"from:{d}" for d in domains[:6])
    return {"query": f"({senders}) {when}", "label": f"from {', '.join(domains[:6])} {label}"}


def gmail_fetcher(ro: ReadOnlyMcp, query: Union[str, Callable[[], str]] = DEFAULT_SCOPE["query"]
                  ) -> Callable[[], List[dict]]:
    """Recent threads -> one {id, from, subject, body} per message. Tool/arg names are fitted to the server's schema
    (read-only names only); search results carry no bodies, so each thread is read with the get tool. Bodies go
    only to `digest_emails` (quarantine)."""
    def fetch() -> List[dict]:
        from src.data_ingestion.gmail_bills import _Args, _items, _pick

        args = _Args(ro)
        search, get = args.tool("search_threads", _Args.SEARCH), args.tool("get_thread", _Args.GET)
        q = query() if callable(query) else query
        text = ro.call(search, args.build(search, {q: ("query", "q")}))
        threads = _items(text, "threads", "messages", "results")
        if not threads and text.strip() and not text.lstrip().startswith(("{", "[")):
            return [{"id": "0", "body": text}]
        out: List[dict] = []
        for t in threads[:DIGEST_THREADS]:
            tid = re.sub(r"[^A-Za-z0-9_-]", "", str(t.get("id", "")))[:40]
            if not tid:
                continue
            try:
                body = ro.call(get, args.build(get, {tid: ("threadId", "thread_id", "messageId", "message_id", "id"),
                                                     "PLAIN_TEXT": ("messageFormat", "format")}))
            except Exception:
                continue  # one unreadable thread must not lose the digest
            msgs = _items(body, "messages") or (_items(f"[{body}]") if body.lstrip().startswith("{") else [])
            for m in msgs[-1:]:  # newest message of the thread
                out.append({"id": m.get("id") or tid, "from": _pick(m, "sender", "from", "From") or "",
                            "subject": _pick(m, "subject") or "",
                            "body": _pick(m, "plaintextBody", "plainTextBody", "textBody", "body", "text", "snippet") or ""})
        return out
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
        ex = extract_facts(raw, f"gmail:{eid}", EmailFacts, client, max_chars=2500)  # digest needs the gist only
        if ex.flagged:
            _flag(vault, f"gmail:{eid}")
        if ex.facts is None:
            out.append(Finding(agent="email", claim_key=f"email:{eid}", summary="Could not safely extract; ignored.",
                               confidence=0, status="uncertain", trust="untrusted"))
            continue
        f = ex.facts
        from src.data_ingestion.gmail_bills import DOMAIN, _sender_domain

        dom = _sender_domain(e.get("from"))  # code-parsed header beats the model's reading of it
        out.append(Finding(agent="email", claim_key=f"email:{eid}", value=f.amount,
                           summary=f"{f.category} from {dom if DOMAIN.match(dom) else f.sender_domain}" + (f" due {f.due_date}" if f.due_date else "")
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
