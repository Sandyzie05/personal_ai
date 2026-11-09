"""Deterministic answer-time grounding: figures, dates and citations in an answer must come from the cited context.

No model involved. Catches the most damaging RAG failure (a confident, fabricated number); it cannot judge prose."""

import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Set

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MON = "|".join(m[:3] + "[a-z]*" for m in _MONTHS)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LONG = re.compile(rf"\b({_MON})\.?\s+(?:(\d{{1,2}})(?:st|nd|rd|th)?,?\s+)?(\d{{4}})\b", re.I)
_NUM = re.compile(r"(?<![\w.])-?\$?\s?-?\d[\d,]*(?:\.\d+)?%?")
_CITE = re.compile(r"\[(ev_[0-9a-f]{8})\]")
_LABEL = re.compile(r"^\[Source \d+[^\]\n]*\bid=(ev_[0-9a-f]{8})\]", re.M)


def _month(name: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(name.lower()[:3]))


def _dates(text: str) -> Set[str]:
    out = {f"{y}-{m}-{d}" for y, m, d in _ISO.findall(text)}
    out |= {f"{y}-{m}" for y, m, _ in _ISO.findall(text)}
    for mon, day, year in _LONG.findall(text):
        out.add(f"{year}-{_month(mon):02d}")
        if day:
            out.add(f"{year}-{_month(mon):02d}-{int(day):02d}")
    return out


def _numbers(text: str) -> Dict[Decimal, str]:
    """Numeric tokens outside dates/citations -> Decimal (so $1,234.50 == 1234.5) with the original token."""
    text = _CITE.sub(" ", _ISO.sub(" ", _LONG.sub(" ", text)))
    out: Dict[Decimal, str] = {}
    for tok in _NUM.findall(text):
        raw = tok.strip()
        digits = re.sub(r"[,$%\s]", "", raw)
        try:
            val = Decimal(digits).copy_abs()
        except InvalidOperation:
            continue
        # bare small integers are counts/list markers/day numbers; money-looking or larger figures are checked
        if "$" not in raw and "." not in raw and "%" not in raw and val <= 31:
            continue
        out.setdefault(val.normalize(), raw)
    return out


def evidence_ids(context: str) -> List[str]:
    return _LABEL.findall(context or "")


def _blocks(context: str) -> Dict[str, str]:
    parts = re.split(r"(?m)^(?=\[Source \d+)", context or "")
    out = {}
    for p in parts:
        m = _LABEL.match(p)
        if m:
            out[m.group(1)] = p
    return out


@dataclass
class GroundingResult:
    ungrounded: List[str] = field(default_factory=list)  # figures/dates not in the cited context
    bad_citations: List[str] = field(default_factory=list)  # cited ids not present in the context
    uncited: List[str] = field(default_factory=list)  # sentences with a figure and no valid citation
    derived: List[str] = field(default_factory=list)  # figures that are a sum/difference of context figures

    @property
    def ok(self) -> bool:
        return not (self.ungrounded or self.bad_citations or self.uncited)

    def flagged(self) -> List[str]:
        return self.ungrounded + [f"[{c}]" for c in self.bad_citations] + self.uncited


def _derivable(val: Decimal, pool: Dict[Decimal, str]) -> bool:
    """val is a sum of 2-3, or the difference of 2, money figures from the context. Only tried for a small pool:
    with many figures coincidental matches would let a fabricated number through."""
    nums = [v for v, raw in pool.items() if "$" in raw or "." in raw]
    if len(nums) > 15:
        return False
    for n in (2, 3):
        if any(sum(c) == val for c in itertools.combinations(nums, n)):
            return True
    return any(abs(a - b) == val for a, b in itertools.combinations(nums, 2))


def check(answer: str, context: Optional[str]) -> GroundingResult:
    """Verify `answer` against `context` (the labelled blocks the model was given)."""
    res = GroundingResult()
    if not context or not answer:
        return res
    ids = set(evidence_ids(context))
    blocks = _blocks(context)
    cited = _CITE.findall(answer)
    res.bad_citations = sorted({c for c in cited if ids and c not in ids})
    valid = [c for c in cited if c in blocks]
    scope = "\n".join(blocks[c] for c in valid) if valid else context  # numbers must occur in what they cite
    ctx_nums, ctx_dates = _numbers(scope), _dates(scope)
    all_nums = _numbers(context)
    for val, raw in _numbers(answer).items():
        if val in ctx_nums:
            continue
        if _derivable(val, all_nums):
            res.derived.append(raw)
        else:
            res.ungrounded.append(raw)
    for d in sorted(_dates(answer)):
        # "2026-03" is grounded by any full date in that month; a full date must appear exactly
        if d not in ctx_dates and not (len(d) == 7 and any(c.startswith(d) for c in ctx_dates)):
            res.ungrounded.append(d)
    if ids:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", answer):
            if re.search(r"\$\s?\d|\d+\.\d\d", _CITE.sub("", sent)) and not any(c in ids for c in _CITE.findall(sent)):
                res.uncited.append(sent.strip()[:80])
    return res


def note(res: GroundingResult) -> str:
    return "\n\n⚠ Could not verify against your documents: " + "; ".join(res.flagged()[:6])
