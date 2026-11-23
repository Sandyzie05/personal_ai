"""Gmail bill sync from email TEXT (most utilities email the total, not a PDF). Read-only MCP (`ReadOnlyMcp`).

Code decides who a bill is from: only messages whose sender domain is on the user's allowlist are read, and the
provider/category come from that allowlist entry, never from the model. The email body is untrusted: it reaches a
model only through `quarantine.extract_facts` (tool-less, strict closed schema, S1). What is stored is a code-templated
record (provider, period, total, due date) plus a manifest entry; the email body itself is never stored or indexed."""

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
MAX_THREADS = 60
LOOKBACK_DAYS = 400
HINT = (
    "This is an email from a utility or phone company. is_bill=true only if it states an amount due for a billing "
    "statement (not marketing, outage, payment-received or autopay-failure notices). total_due = the amount due "
    "as printed. Dates as YYYY-MM-DD; leave a field null when the email does not state it."
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


def sync(
    vault,
    ro,
    client,
    on_indexed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    today: Optional[date] = None,
    only: Optional[str] = None,
) -> pipeline.IngestReport:
    """Search allowlisted senders, read each new message once, extract bill facts via quarantine, store records.
    `only`: sync just that provider's sender row."""
    r = pipeline.IngestReport()
    rows = [s for s in senders(vault) if only is None or s["provider"] == only]
    if not rows:
        r.needs_you.append("add a bill sender first")
        return r
    args = _Args(ro)
    search, get = (
        args.tool("search_threads", _Args.SEARCH),
        args.tool("get_thread", _Args.GET),
    )
    query = (
        "("
        + " OR ".join(f"from:{s['domain']}" for s in rows)
        + f") newer_than:{LOOKBACK_DAYS}d"
    )
    threads = _items(
        ro.call(search, args.build(search, {query: ("query", "q")})),
        "threads",
        "messages",
        "results",
    )
    docs = pipeline._manifest(vault)
    seen = {"msgs": 0, "allowed": 0, "not_bill": 0}
    for t in threads[:MAX_THREADS]:
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
            continue
        msgs = _items(thread, "messages")
        if not msgs:  # a single-message tool returns the message object itself
            one = _items(f"[{thread}]") if thread.lstrip().startswith("{") else []
            msgs = [m for m in one if "messages" not in m]
        for m in msgs:
            mid = re.sub(r"[^A-Za-z0-9_-]", "", str(m.get("id", "")))[:40] or tid
            sha = hashlib.sha256(f"gmail:{mid}".encode()).hexdigest()
            if sha in docs:
                r.duplicates += 1
                continue
            seen["msgs"] += 1
            entry = _match(_sender_domain(_pick(m, "sender", "from", "From")), rows)
            if entry is None:
                continue  # not an allowlisted sender: never read
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
            if f is None or not f.is_bill or f.total_due is None:
                seen["not_bill"] += 1
                docs[sha] = {
                    "sha256": sha,
                    "source": "gmail",
                    "status": "skipped",
                    "provider": entry["provider"],
                    "fetched_at": datetime.now().isoformat(),
                }
                continue
            end = f.period_end or f.statement_date or (today or date.today())
            if end > (today or date.today()) + timedelta(days=45):
                end = f.statement_date or (
                    today or date.today()
                )  # a due date is not a period end
            dup = any(
                d.get("status") == "indexed"
                and d.get("provider") == entry["provider"]
                and d.get("period_end") == end.isoformat()
                for d in docs.values()
            )
            if dup:
                r.duplicates += 1
                docs[sha] = {
                    "sha256": sha,
                    "source": "gmail",
                    "status": "duplicate",
                    "provider": entry["provider"],
                    "fetched_at": datetime.now().isoformat(),
                }
                continue
            key = f"gmail_bill_{sha[:16]}"
            text = (
                f"{entry['provider']} {entry['category']} bill (from email). Billing period "
                f"{f.period_start or 'not stated'} to {end}. Total due ${f.total_due:,.2f}."
                + (f" Due {f.due_date}." if f.due_date else "")
            )
            data = {
                "metadata": {
                    "original_filename": f"gmail-{sha[:12]}.txt",
                    "file_type": "txt",
                    "content_type": "text",
                    "upload_timestamp": datetime.now().isoformat(),
                    "source": "gmail",
                    "category": entry["category"],
                    "provider": entry["provider"],
                    "period_start": f.period_start.isoformat()
                    if f.period_start
                    else None,
                    "period_end": end.isoformat(),
                    "trust": "untrusted",
                },
                "text_content": text,
                "extraction": {
                    "provider": entry["provider"],
                    "account_identifier": None,
                    "period_start": f.period_start.isoformat()
                    if f.period_start
                    else None,
                    "period_end": end.isoformat(),
                    "total": f.total_due,
                    "line_items": [],
                },
            }
            vault.store_data(key, data, encrypt=True)
            if on_indexed:
                on_indexed(key, data)
            docs[sha] = {
                "sha256": sha,
                "source": "gmail",
                "source_ref": f"gmail:{mid}",
                "provider": entry["provider"],
                "category": entry["category"],
                "period_start": data["extraction"]["period_start"],
                "period_end": end.isoformat(),
                "fetched_at": datetime.now().isoformat(),
                "status": "indexed",
                "storage_key": key,
            }
            r.new.append(f"{entry['provider']} {end.isoformat()}")
    pipeline._save(vault, docs)
    if not threads:
        r.needs_you.append("no email from " + ", ".join(x["domain"] for x in rows) + " in the last 13 months: check the "
                           "sender domain spelling (open a bill email, copy the part after @; re-adding the provider "
                           "replaces the row)")
    r.checked = (f"searched {len(threads)} thread(s) from {', '.join(x['domain'] for x in rows)}; "
                 f"{seen['allowed']} new message(s) from allowlisted senders, {seen['not_bill']} not a bill")
    return r
