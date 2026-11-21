"""Approval-gated Core Memory.

Agents may only `propose`. `approve` needs a single-use, session-bound token minted by the UI button handler and
records provenance; there is no agent-facing commit path. Corrections supersede (never append
a contradiction). Proposals derived from untrusted content need an extra acknowledgement.
"""

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agents.store import _load, _save
from .filelock import locked

_LOCK = lambda: locked("memory")  # load-modify-save must not interleave across sessions, threads or processes
MAX_PENDING = 50
PENDING_KEY = "core_memory_pending"
CORE_KEY = "core_memory"


_TOKENS: Dict[str, tuple] = {}  # token -> (session_id, item_id, purpose); in-process, single-use


class ApprovalError(Exception):
    """Core Memory change attempted without valid user approval."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose(vault: Any, key: str, value: str, *, proposer: str, evidence_ids=(), trust="trusted") -> str:
    """Queue a change; returns the pending id. Nothing is committed. Identical proposals dedupe."""
    if trust not in ("trusted", "untrusted"):
        raise ValueError("trust must be 'trusted' or 'untrusted'")
    with _LOCK():
        return _propose(vault, key, value, proposer, evidence_ids, trust)


def _propose(vault, key, value, proposer, evidence_ids, trust) -> str:
    pending = _load(vault, PENDING_KEY, {"items": []})
    for existing in pending["items"]:
        if existing["key"] == key[:80] and existing["value"] == str(value)[:500] and existing["trust"] == trust:
            return existing["id"]
    item = {"id": uuid.uuid4().hex, "key": key[:80], "value": str(value)[:500], "proposer": proposer,
            "evidence_ids": list(evidence_ids), "trust": trust, "created_at": _now()}
    pending["items"] = (pending["items"] + [item])[-MAX_PENDING:]  # bounded queue, oldest dropped
    _save(vault, PENDING_KEY, pending)
    return item["id"]


def list_pending(vault: Any) -> List[Dict[str, Any]]:
    return list(_load(vault, PENDING_KEY, {"items": []})["items"])


def list_core(vault: Any, include_superseded: bool = False) -> List[Dict[str, Any]]:
    items = _load(vault, CORE_KEY, {"items": []})["items"]
    return items if include_superseded else [i for i in items if not i.get("superseded_by")]


def issue_approval_token(session_id: str, pending_id: str, purpose: str = "approve") -> str:
    """Mint a single-use token bound to one UI session, one item and one purpose ("approve" a pending item or
    "forget" a committed one). Called ONLY from the Core Memory button handlers (interface/agents.py); a test
    asserts no other module in src/ references it."""
    if purpose not in ("approve", "forget"):
        raise ValueError("purpose must be 'approve' or 'forget'")
    token = secrets.token_urlsafe(24)
    with _LOCK():
        if len(_TOKENS) > 200:  # clicks that never reached approve()/forget(): drop the oldest
            _TOKENS.pop(next(iter(_TOKENS)))
        _TOKENS[token] = (session_id, pending_id, purpose)
    return token


def approve(vault: Any, pending_id: str, *, token: str, session_id: str, acknowledge_untrusted: bool = False) -> Dict[str, Any]:
    with _LOCK():
        bound = _TOKENS.pop(token, None)  # consumed even when it fails: never replayable
        if bound != (session_id, pending_id, "approve"):
            raise ApprovalError("valid single-use approval token for this session and item required")
        return _approve(vault, pending_id, acknowledge_untrusted)


def _approve(vault, pending_id, acknowledge_untrusted) -> Dict[str, Any]:
    pending = _load(vault, PENDING_KEY, {"items": []})
    item = next((i for i in pending["items"] if i["id"] == pending_id), None)
    if item is None:
        raise ApprovalError("unknown pending item")
    if item["trust"] != "trusted" and not acknowledge_untrusted:  # fail closed on unknown trust values
        raise ApprovalError("derived from untrusted content: explicit acknowledgement required")
    core = _load(vault, CORE_KEY, {"items": []})
    committed = {**item, "approved_at": _now(), "approved_by": "user", "superseded_by": None}
    for old in core["items"]:
        if old["key"] == item["key"] and not old.get("superseded_by"):
            old["superseded_by"] = committed["id"]
    core["items"].append(committed)
    pending["items"] = [i for i in pending["items"] if i["id"] != pending_id]
    _save(vault, CORE_KEY, core)
    _save(vault, PENDING_KEY, pending)
    return committed


def reject(vault: Any, pending_id: str) -> bool:
    with _LOCK():
        return _reject(vault, pending_id)


def _reject(vault, pending_id) -> bool:
    pending = _load(vault, PENDING_KEY, {"items": []})
    kept = [i for i in pending["items"] if i["id"] != pending_id]
    changed = len(kept) != len(pending["items"])
    _save(vault, PENDING_KEY, {"items": kept})
    return changed


def forget(vault: Any, item_id: str, *, token: str, session_id: str) -> bool:
    """Delete a committed item (not supersede it): it is never recalled again. Same UI-minted token gate as approve."""
    with _LOCK():
        bound = _TOKENS.pop(token, None)
        if bound != (session_id, item_id, "forget"):
            raise ApprovalError("valid single-use forget token for this session and item required")
        core = _load(vault, CORE_KEY, {"items": []})
        kept = [i for i in core["items"] if i["id"] != item_id]
        if len(kept) == len(core["items"]):
            return False
        # Older versions it superseded stay superseded (pointing at a deleted id), so forgetting never resurrects them.
        _save(vault, CORE_KEY, {"items": kept})
        return True


_WORD = re.compile(r"[a-z0-9]{3,}|q[1-4]")
_STOP = {"the", "and", "for", "what", "was", "were", "how", "much", "did", "does", "with", "from", "this", "that",
         "last", "about", "have", "show", "tell", "give", "your"}


def _words(text: str) -> set:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower())]
    out = {t for t in toks if _WORD.fullmatch(t) and t not in _STOP}
    out |= {a + b for a, b in zip(toks, toks[1:]) if len(a + b) >= 6}  # "cash flow" also matches "cashflow"
    return out


def recall(vault: Any, query: str, limit: int = 5):
    """Approved, current (not superseded, not forgotten) items relevant to `query`, as `CoreFact`s.

    Read-only and deterministic: score = shared words between the query and the item key (key parts split on
    ':' '_' '-'), ties broken by newest approval then key. Untrusted-derived items keep trust="untrusted"."""
    from .schemas import CoreFact

    words = _words(str(query))
    scored = []
    for item in list_core(vault):
        if item.get("approved_by") != "user" or not item.get("approved_at"):
            continue
        key_words = _words(re.sub(r"[:_\-]", " ", item["key"]))
        score = len(words & key_words)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda p: (-p[0], _neg_time(p[1]["approved_at"]), p[1]["key"]))
    out = []
    for _, item in scored[:max(0, limit)]:
        out.append(CoreFact(key=item["key"][:80], value=str(item["value"])[:200], approved_at=item["approved_at"],
                            trust="trusted" if item.get("trust") == "trusted" else "untrusted"))
    return out


def _neg_time(iso: str) -> float:
    return -datetime.fromisoformat(iso).timestamp()
