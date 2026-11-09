"""Request policy: out-of-scope actions are refused before any planning.

Trading, payments, shell commands, medical diagnosis and secret disclosure are outside this
system's scope (docs/platform_plan.md §3.3). Research *questions* about a trade ("should I buy
NVDA?") are allowed; imperative execution requests are not. Regexes are a first gate only: the
real controls are tool allowlists and read-only MCP wrappers, so a missed phrasing still cannot act.
"""

import re
from dataclasses import dataclass
from typing import Optional

_LEAD = r"(?:^|[.;!?]\s*|\b(?:and|then|please|now|just)\s+|\b(?:can|could|would|will) you\s+|go ahead and\s+)"
RULES = (
    (
        "trade",
        re.compile(
            _LEAD + r"(?:buy|sell|short|purchase|liquidate|dump|go\s+long|go\s+short)\b|"
        r"\b(?:close|exit|open)\b.{0,15}\bpositions?\b|"
            r"\b(?:place|submit|execute|cancel|enter)\b.{0,20}\b(?:order|trade)s?\b",
            re.I,
        ),
    ),
    (
        "payment",
        re.compile(
            _LEAD
            + r"(?:pay|wire|transfer|send|remit)\b.{0,40}(?:\$\s?\d|\bbill\b|\brent\b|\baccount\b|\bcard\b|\bloan\b)",
            re.I,
        ),
    ),
    (
        "command",
        re.compile(
            r"\b(?:rm\s+-\w+|sudo|chmod|curl\s+http|wget\s|bash\s+-c|sh\s+-c|powershell|os\.system|subprocess)\b|"
            r"\b(?:run|execute)\s+(?:this|the|a|my)?\s*(?:shell|bash|terminal|script|command)\b",
            re.I,
        ),
    ),
    (
        "medical",
        re.compile(
            r"\b(?:diagnos\w+|prescrib\w+|what (?:medication|dosage|dose|drug))\b", re.I
        ),
    ),
    (
        "secret",
        re.compile(
            r"\b(?:reveal|show|print|dump|export|leak|send|email|forward|post|upload|paste|copy)\b.{0,30}\b(?:password|vault key|encryption key|dek|api key|secret|token|credentials?)\b",
            re.I,
        ),
    ),
)
MESSAGES = {
    "trade": "I can research a ticker and explain evidence and uncertainty, but I can't place, cancel or recommend executing trades.",
    "payment": "I can summarise bills and cash flow, but I can't make payments or transfers.",
    "command": "I can't run commands or scripts.",
    "medical": "I can summarise your health records against your own targets, but I can't diagnose or advise on treatment.",
    "secret": "I can't reveal or export credentials, keys or tokens.",
}


class RequestRefused(ValueError):
    """Raised for out-of-scope requests; str() is user-safe text."""

    def __init__(self, kind: str):
        super().__init__(MESSAGES[kind])
        self.kind = kind


@dataclass(frozen=True)
class Refusal:
    kind: str
    message: str


def screen(query: str) -> Optional[Refusal]:
    text = query or ""
    for kind, pat in RULES:
        if pat.search(text):
            return Refusal(kind, MESSAGES[kind])
    return None
