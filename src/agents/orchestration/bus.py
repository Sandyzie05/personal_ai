"""Least-disclosure message bus.

Every hop must match a typed schema for its (sender, receiver) edge and survive a PII/canary
scan on string fields. Violations are dropped and audited; only field names + a payload hash
are logged (never the payload).
"""

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, ValidationError

from src.agents import store
from .schemas import EDGES

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "long_number": re.compile(r"\b\d{8,17}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b[\w.+-]+\s*(?:\(at\)|\[at\]|\bat\b)\s*[\w-]+\s*(?:\(dot\)|\[dot\]|\bdot\b)\s*\w+", re.I),
    "phone": re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
}


class DisclosureViolation(Exception):
    """Outgoing agent message violated its channel schema or disclosure scan."""


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


class MessageBus:
    def __init__(self, vault: Any = None, edges: Optional[Dict[Tuple[str, str], type]] = None,
                 canaries: Iterable[str] = ()):
        self.vault = vault
        self.edges = edges if edges is not None else EDGES
        self.canaries = tuple(c for c in canaries if c)
        self.delivered: List[Dict[str, Any]] = []
        self._sent: Dict[Tuple[str, str, str], BaseModel] = {}  # coordinator retries must not re-deliver

    def send(self, sender: str, receiver: str, payload: Any) -> BaseModel:
        schema = self.edges.get((sender, receiver))
        if schema is None:
            self._audit(sender, receiver, "-", (), "blocked", "no channel")
            raise DisclosureViolation(f"no channel {sender}->{receiver}")
        try:
            msg = schema.model_validate(payload)
        except ValidationError as exc:
            fields = tuple(payload) if isinstance(payload, dict) else ()
            self._audit(sender, receiver, schema.__name__, fields, "blocked", "schema")
            raise DisclosureViolation(f"{schema.__name__}: {exc.errors()[0]['type']}") from None
        key = (sender, receiver, hashlib.sha256(msg.model_dump_json().encode()).hexdigest()[:16])
        if key in self._sent:  # identical payload already delivered (a retried worker re-sending): no duplicate
            return self._sent[key]
        data = msg.model_dump(mode="json")
        for text in _strings(data):
            squeezed = re.sub(r"(?<=\d)[ .-](?=\d)", "", text)  # 4111-1111-1111-1111 -> 16 digits
            for name, pat in PII_PATTERNS.items():
                if pat.search(text) or (name == "long_number" and pat.search(squeezed)):
                    self._audit(sender, receiver, schema.__name__, tuple(data), "blocked", f"pii:{name}")
                    raise DisclosureViolation(f"{name} pattern in outgoing message")
            if any(c in text for c in self.canaries):
                self._audit(sender, receiver, schema.__name__, tuple(data), "blocked", "canary")
                raise DisclosureViolation("canary token in outgoing message")
        digest = hashlib.sha256(msg.model_dump_json().encode()).hexdigest()[:16]
        self._audit(sender, receiver, schema.__name__, tuple(data), "delivered", "", digest)
        self._sent[(sender, receiver, digest)] = msg
        return msg

    def _audit(self, sender, receiver, schema, fields, outcome, reason, digest=""):
        entry = {"kind": "bus", "sender": sender, "receiver": receiver, "schema": schema,
                 "fields": list(fields), "outcome": outcome, "reason": reason, "sha": digest}
        self.delivered.append(entry)
        if self.vault is not None:
            store.log_activity(self.vault, entry)
