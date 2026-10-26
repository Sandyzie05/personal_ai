"""Approval-gated Core Memory.

Agents may only `propose`. `approve` needs a single-use, session-bound token minted by the UI button handler and
records provenance; there is no agent-facing commit path. Corrections supersede (never append
a contradiction). Proposals derived from untrusted content need an extra acknowledgement.
"""

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


_TOKENS: Dict[str, tuple] = {}  # token -> (session_id, pending_id); in-process, single-use


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


def issue_approval_token(session_id: str, pending_id: str) -> str:
    """Mint a single-use token bound to one UI session and one pending item. Called ONLY from the Core Memory
    button handler (interface/agents.py); a test asserts no other module in src/ references it."""
    token = secrets.token_urlsafe(24)
    with _LOCK():
        if len(_TOKENS) > 200:  # clicks that never reached approve(): drop the oldest
            _TOKENS.pop(next(iter(_TOKENS)))
        _TOKENS[token] = (session_id, pending_id)
    return token


def approve(vault: Any, pending_id: str, *, token: str, session_id: str, acknowledge_untrusted: bool = False) -> Dict[str, Any]:
    with _LOCK():
        bound = _TOKENS.pop(token, None)  # consumed even when it fails: never replayable
        if bound != (session_id, pending_id):
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
