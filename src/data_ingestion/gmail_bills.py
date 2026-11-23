"""Gmail bill sync from email TEXT (most utilities email the total, not a PDF). Read-only MCP (`ReadOnlyMcp`).

Code decides who a bill is from: only messages whose sender domain is on the user's allowlist are read, and the
provider/category come from that allowlist entry, never from the model. The email body is untrusted: it reaches a
model only through `quarantine.extract_facts` (tool-less, strict closed schema, S1). What is stored is a manifest-only
bill notice (provider, period, emailed total/due as typed numbers; S0): no vault document, nothing in Files, Dashboard or
RAG. The email body itself is never stored or indexed. Official PDFs are the only bill documents."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.agents.orchestration.quarantine import extract_facts
from src.data_ingestion import pipeline

SENDERS_KEY = "gmail_bill_senders"
DOMAIN = re.compile(r"^(?:[a-z0-9-]{1,40}\.){1,4}[a-z]{2,12}$")
PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&'-]{0,39}$")
MAX_THREADS = 15  # newest first; each new bill email is one model call (older ones are cached)
LOOKBACK_DAYS = 400
HINT = (
    "This is an email from a utility or phone company. is_bill=true if it is a billing statement or says a new bill "
    "is ready, even when no amount is shown (false for marketing, outage, payment-received or autopay-failure "
    "notices). total_due = the amount due as printed, null when the email does not show it. Dates as YYYY-MM-DD; leave a field null when the email does not state it."
)


class BillEmailFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_bill: bool
    total_due: Optional[float] = Field(default=None, ge=0, le=100_000)
    due_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    statement_date: Optional[date] = None


def senders(vault) -> List[dict]:
    return list((vault.retrieve_data(SENDERS_KEY) or {}).get("senders", []))


def add_sender(vault, provider: str, category: str, domain: str) -> None:
    from src.data_extraction.categories import category_keys

    domain = domain.strip().lower().lstrip("@")
    if (
        not PROVIDER.match(provider.strip())
        or not DOMAIN.match(domain)
        or category not in category_keys()
    ):
        raise ValueError("provider, category or sender domain is not valid")
    rows = [s for s in senders(vault) if s["provider"] != provider.strip()]
    rows.append({"provider": provider.strip(), "category": category, "domain": domain})
    vault.store_data(SENDERS_KEY, {"senders": rows}, encrypt=True)
    if provider.strip() not in pipeline.expectations(vault):
        pipeline.set_expectation(vault, provider.strip(), "monthly")


def remove_sender(vault, provider: str) -> None:
    vault.store_data(
        SENDERS_KEY,
        {"senders": [s for s in senders(vault) if s["provider"] != provider]},
        encrypt=True,
    )


def _sender_domain(value: Any) -> str:
    m = re.search(r"[\w.+-]+@([\w.-]+)", str(value or ""))
    return m.group(1).lower() if m else ""


def _match(domain: str, rows: List[dict]) -> Optional[dict]:
    return next(
        (
            s
            for s in rows
            if domain == s["domain"] or domain.endswith("." + s["domain"])
        ),
        None,
    )


def _sent_date(m: dict) -> Optional[date]:
    """Message send date from the Date header (RFC 2822) or internalDate (epoch ms); None if unparseable."""
    from email.utils import parsedate_to_datetime

    raw = _pick(m, "date", "Date", "internalDate")
    try:
        if isinstance(raw, (int, float)) or str(raw).isdigit():
            return datetime.fromtimestamp(int(raw) / 1000).date()
        return parsedate_to_datetime(str(raw)).date() if raw else None
    except (TypeError, ValueError, OverflowError):
        return None


def reset(vault, provider: str, on_removed: Optional[Callable[[str], None]] = None) -> int:
    """Forget what Gmail sync stored for one provider (records + read-once ledger) so the next Sync re-reads its
    emails. Only gmail-sourced entries are touched; uploaded PDFs stay."""
    docs = pipeline._manifest(vault)
    drop = [k for k, d in docs.items() if d.get("source") == "gmail" and d.get("provider") == provider]
    for k in drop:
        key = docs.pop(k).get("storage_key")
        if key:
            vault.delete_data(key)
            if on_removed:
                on_removed(key)
    pipeline._save(vault, docs)
    return len(drop)


def _pick(d: dict, *names):
    for n in names:
        v = d.get(n)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return v
    return None


class _Args:
    """Fit our tool and arg names to the server's schema (they differ between Gmail MCP servers). Only read-only
    tool names are ever picked; ReadOnlyMcp re-checks every call."""

    SEARCH = re.compile(r"^(search|list)_(threads|messages|emails)$")
    GET = re.compile(r"^(get|read)_(thread|message|email)$")

    def __init__(self, ro):
        try:
            self.schemas = {
                t.name: (t.input_schema or {}).get("properties") or {}
                for t in ro.mcp.list_tools(ro.server_id)
            }
        except Exception:
            self.schemas = {}

    def tool(self, preferred: str, pattern) -> str:
        if not self.schemas or preferred in self.schemas:
            return preferred
        return next((n for n in sorted(self.schemas) if pattern.match(n)), preferred)

    def build(self, tool: str, wanted: Dict[str, tuple]) -> dict:
        props, out = self.schemas.get(tool) or {}, {}
        for value, names in wanted.items():
            name = next((n for n in names if n in props), None) if props else names[0]
            enum = (
                (props.get(name) or {}).get("enum")
                if name and isinstance(props.get(name), dict)
                else None
            )
            if (
                enum and value not in enum
            ):  # e.g. format enum without PLAIN_TEXT: take the closest, else omit
                value = next(
                    (e for e in enum if re.search(r"plain|text|full", str(e), re.I)),
                    None,
                )
            if name and value is not None:
                out[name] = value
        return out


def _items(text: str, *keys) -> List[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    for k in keys:
        if isinstance(data, dict) and isinstance(data.get(k), list):
            return [x for x in data[k] if isinstance(x, dict)]
    return []


def migrate_text_documents(vault, on_removed: Optional[Callable[[str], None]] = None) -> int:
    """S0 one-time migration: email-text bill documents (`gmail_bill_*`, shown as gmail-*.txt) become notices; the
    documents and their RAG entries are removed. Uploaded/portal PDFs are untouched. Idempotent."""
    docs, n = pipeline._manifest(vault), 0
    for d in docs.values():
        key = d.get("storage_key") or ""
        if d.get("source") != "gmail" or d.get("status") != "indexed" or not key.startswith("gmail_bill_"):
            continue
        ext = (vault.retrieve_data(key) or {}).get("extraction") or {}
        d.update(status="notice", emailed_total=ext.get("total"), emailed_due=None)
        d.pop("storage_key", None)
        vault.delete_data(key)
        if on_removed:
            on_removed(key)
        n += 1
    if n:
        pipeline._save(vault, docs)
    return n


def _set_newest(vault, newest: Dict[str, str]) -> None:
    rows = senders(vault)
    for s in rows:
        if newest.get(s["provider"], "") > s.get("newest_seen", ""):
            s["newest_seen"] = newest[s["provider"]]
    vault.store_data(SENDERS_KEY, {"senders": rows}, encrypt=True)


def sync(vault, ro, client, on_indexed=None, today: Optional[date] = None, only: Optional[str] = None,
         progress=None) -> pipeline.IngestReport:
    """Traced wrapper (Track O): counts and fixed outcome names only, never provider names or email text."""
    from collections import Counter

    from src.observability import otel

    with otel.span("sync gmail", model=str(getattr(client, "model", "") or "")) as sp:
        r = _sync(vault, ro, client, on_indexed, today, only, progress)
        c = Counter(o["outcome"].split(" ")[0] for o in r.outcomes)
        sp.set(new=len(r.new), duplicates=r.duplicates, notices=c.get("no", 0), not_bill=c.get("not", 0),
               failed=len(r.failed), model_calls=r.model_calls, threads=len(r.outcomes))
        return r


def _sync(
    vault,
    ro,
    client,
    on_indexed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    today: Optional[date] = None,
    only: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> pipeline.IngestReport:
    """Search each allowlisted sender (newest MAX_THREADS threads each), read each new message once, extract bill
    facts via quarantine, store records. `only`: sync just that provider's sender row. `r.outcomes` gets one
    code-built row per message seen (date, provider, validated domain, an OUTCOMES value): never subject/body."""
    r = pipeline.IngestReport()
    migrate_text_documents(vault, (lambda k: on_indexed(k, None)) if on_indexed else None)
    rows = [s for s in senders(vault) if only is None or s["provider"] == only]
    if not rows:
        r.needs_you.append("add a bill sender first")
        return r
    today = today or date.today()
    model = str(getattr(client, "model", "") or "")
    say = progress or (lambda _msg: None)  # code-built status text only (counts, provider names)
    args = _Args(ro)
    search, get = (
        args.tool("search_threads", _Args.SEARCH),
        args.tool("get_thread", _Args.GET),
    )
    # One search per sender so a chatty sender cannot crowd another one out of the thread cap.
    threads: List[tuple] = []
    for row in rows:
        found = _items(ro.call(search, args.build(search, {
            f"from:{row['domain']} newer_than:{LOOKBACK_DAYS}d": ("query", "q"),
            MAX_THREADS: ("pageSize", "maxResults")})), "threads", "messages", "results")
        threads += [(row, t) for t in found[:MAX_THREADS]]
        if not found:
            r.needs_you.append(f"no email from {row['domain']} in the last 13 months: check the sender domain "
                               "spelling (open a bill email, copy the part after @; re-adding the provider replaces "
                               "the row)")
    docs = pipeline._manifest(vault)
    seen = {"allowed": 0, "not_bill": 0, "notice": 0, "model_calls": 0}
    newest: Dict[str, str] = {}
    say(f"Found {len(threads)} thread(s); reading up to {MAX_THREADS} per sender")

    def out(row_provider: str, dom: str, sent: Optional[date], outcome: str) -> None:
        r.outcomes.append({"sent": sent.isoformat() if sent else "", "provider": row_provider,
                           "domain": dom if DOMAIN.match(dom) else "", "outcome": outcome})

    for n, (row, t) in enumerate(threads, 1):
        say(f"Thread {n}/{len(threads)} ({row['provider']}): reading (read-only)")
        tid = re.sub(r"[^A-Za-z0-9_-]", "", str(t.get("id", "")))[:40]
        if not tid:
            continue
        try:
            thread = ro.call(
                get,
                args.build(
                    get,
                    {
                        tid: ("threadId", "thread_id", "messageId", "message_id", "id"),
                        "PLAIN_TEXT": ("messageFormat", "format"),
                    },
                ),
            )
        except Exception as e:  # one unreadable thread must not lose the rest; type only (server text is untrusted)
            r.failed.append(f"thread {tid[:8]}: {type(e).__name__}")
            out(row["provider"], row["domain"], None, "unreadable")
            continue
        msgs = _items(thread, "messages")
        if not msgs:  # a single-message tool returns the message object itself
            one = _items(f"[{thread}]") if thread.lstrip().startswith("{") else []
            msgs = [m for m in one if "messages" not in m]
        for m in msgs:
            mid = re.sub(r"[^A-Za-z0-9_-]", "", str(m.get("id", "")))[:40] or tid
            sha = hashlib.sha256(f"gmail:{mid}".encode()).hexdigest()
            dom = _sender_domain(_pick(m, "sender", "from", "From"))
            sent = _sent_date(m)
            entry = _match(dom, rows)
            if entry is None:
                out("", dom, sent, "other sender")  # not an allowlisted sender: body never read
                continue
            if sent and sent.isoformat() > newest.get(entry["provider"], ""):
                newest[entry["provider"]] = sent.isoformat()
            old = docs.get(sha)
            # "not a bill" verdicts are re-checked when the extractor model changes (they used to be cached forever).
            if old and not (old.get("status") == "skipped" and old.get("model") != model):
                r.duplicates += 1
                no_amount = old.get("status") == "notice" and old.get("emailed_total") is None
                out(entry["provider"], dom, sent, "no amount stated" if no_amount else
                    old.get("outcome") or "not a bill" if old.get("status") == "skipped" else "already saved")
                continue
            seen["allowed"] += 1
            body = (
                _pick(
                    m,
                    "plaintextBody",
                    "plainTextBody",
                    "textBody",
                    "body",
                    "text",
                    "snippet",
                )
                or ""
            )
            raw = f"Subject: {_pick(m, 'subject') or ''}\nDate: {_pick(m, 'date', 'internalDate') or ''}\n\n{body}"
            say(f"Thread {n}/{len(threads)} ({entry['provider']}): extracting bill facts with "
                f"{model or 'the model'}")
            seen["model_calls"] += 1
            ex = extract_facts(
                raw, f"gmail:{mid}", BillEmailFacts, client, extra_instructions=HINT
            )
            if ex.flagged:
                from src.agents import store

                store.log_activity(
                    vault, {"kind": "injection_flagged", "source": "gmail_bill"}
                )
            f = ex.facts
            if ex.error == "unavailable":
                r.failed.append("model unavailable; sync again later")
                break  # not cached: retried on the next sync
            base = {"sha256": sha, "source": "gmail", "provider": entry["provider"], "model": model,
                    "sent": sent.isoformat() if sent else None, "fetched_at": datetime.now().isoformat()}
            if f is None or not f.is_bill:
                seen["not_bill"] += 1
                outcome = "unreadable" if f is None else "not a bill"
                docs[sha] = {**base, "status": "skipped", "outcome": outcome}
                out(entry["provider"], dom, sent, outcome)
                continue
            # Most "your bill is ready" emails state no period; the email's own send date is the statement date
            # then. (Falling back to TODAY made every bill the same period: live, 14 of 15 counted as duplicates.)
            end = f.period_end or f.statement_date or sent or today
            if end > today + timedelta(days=45):
                end = f.statement_date or sent or today  # a due date is not a period end
            # S0: email text is a signal, never a document. A notice is a manifest entry only (provider, period,
            # emailed amount/due as typed numbers); nothing goes to the vault documents, Files, Dashboard or RAG.
            # Official bill PDFs (upload, S4 Gmail attachment, R10 portal) are the only documents.
            notice = {**base, "status": "notice", "category": entry["category"], "source_ref": f"gmail:{mid}",
                      "period_start": f.period_start.isoformat() if f.period_start else None,
                      "period_end": end.isoformat(), "emailed_total": f.total_due,
                      "emailed_due": f.due_date.isoformat() if f.due_date else None}
            same = next((k for k, d in docs.items() if d.get("status") == "notice"
                         and d.get("provider") == entry["provider"] and d.get("period_end") == end.isoformat()), None)
            if same and (f.total_due is None or docs[same].get("emailed_total") is not None):
                r.duplicates += 1
                docs[sha] = {**base, "status": "duplicate"}
                out(entry["provider"], dom, sent, "already saved")
                continue
            if same:  # an earlier no-amount notice for this period: the amount arrived later
                docs.pop(same)
            docs[sha] = notice
            if f.total_due is None:
                seen["notice"] += 1
                out(entry["provider"], dom, sent, "no amount stated")
                continue
            r.new.append(f"{entry['provider']} {end.isoformat()}")
            out(entry["provider"], dom, sent, f"saved period {end.isoformat()}")
    pipeline._save(vault, docs)
    _set_newest(vault, newest)
    r.model_calls = seen["model_calls"]
    r.checked = (f"searched {len(threads)} thread(s) from {', '.join(x['domain'] for x in rows)}; "
                 f"{seen['allowed']} new message(s) from allowlisted senders, {seen['notice']} bill notice(s) with "
                 f"no amount, {seen['not_bill']} not a bill; {seen['model_calls']} model call(s)")
    return r
