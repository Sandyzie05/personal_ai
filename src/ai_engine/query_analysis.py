"""Lightweight heuristics for routing chat questions to structured data.

Deliberately conservative: `detect_category_from_query` only returns a
category when the question unambiguously names exactly one, and
`parse_relative_date_range` only handles a small set of common phrasings.
Anything less certain returns None, so ChatEngine falls back to plain RAG
(or an unfiltered category scan) rather than risk a confidently-wrong
number for a financial question.
"""

import re
from datetime import date, timedelta
from typing import List, Optional, Tuple

from src.data_extraction.categories import CATEGORIES

# Extra aliases beyond each category's own document-classification keywords.
# A question names a category the way a person would ask about it (e.g.
# "robinhood", "solar credit"), which doesn't always match how that concept
# appears inside the bill's own text.
CATEGORY_QUERY_ALIASES = {
    "electricity": ["electricity", "electric bill", "power bill", "solar credit"],
    "gas": ["gas bill", "natural gas"],
    "credit_card": ["credit card"],
    "checking": ["checking account", "bank account", "checking"],
    "brokerage": ["robinhood", "e*trade", "etrade", "brokerage", "stock account"],
    "mobile": [
        "t-mobile",
        "tmobile",
        "verizon",
        "at&t",
        "wireless bill",
        "phone bill",
        "mobile bill",
        "cell phone",
    ],
}


def detect_category_from_query(query: str) -> Optional[str]:
    """Return a category key if the question clearly names exactly one."""
    lowered = query.lower()
    matches = set()
    for category in CATEGORIES:
        aliases = [
            *CATEGORY_QUERY_ALIASES.get(category.key, []),
            category.label.lower(),
        ]
        if any(alias in lowered for alias in aliases):
            matches.add(category.key)

    if len(matches) == 1:
        return matches.pop()
    return None


def detect_provider_from_query(query: str, known_providers: List[str]) -> Optional[str]:
    """Return the one known provider name (verbatim) the question clearly names.

    `known_providers` must come from the vault's own already-uploaded
    documents (e.g. distinct `metadata.provider` values for a category), not
    a static brand list - the point is disambiguating *which* of the user's
    own credit cards/checking accounts a question means, and that set is
    user-specific. Returning the exact stored string (not a normalized
    version) lets callers use it directly as an exact-match filter. Zero or
    more than one match returns None - same conservative bar as
    `detect_category_from_query`.
    """
    lowered = query.lower()
    matches = [p for p in known_providers if p and p.lower() in lowered]
    if len(matches) == 1:
        return matches[0]
    return None


_MONTH_RANGE_RE = re.compile(r"last\s+(\d+)\s+months?", re.IGNORECASE)

_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{4})\b", re.IGNORECASE
)


def parse_relative_date_range(
    query: str, today: Optional[date] = None
) -> Optional[Tuple[str, str]]:
    """Parse simple relative date phrases into an (start_iso, end_iso) range.

    Only handles common, unambiguous phrasings ("last N months", "this
    month", "last month", an explicit "<Month name> <YYYY>") - anything else
    returns None, meaning "no date filter" rather than a guessed range.
    """
    today = today or date.today()
    lowered = query.lower()

    # Every explicit "<Month> <YYYY>" mentioned: one month, or the span covering
    # several ("January 2026 or March 2026" -> Jan 1..Mar 31).
    spans = [
        (date(int(y), _MONTH_NAMES[mo], 1), date(int(y), _MONTH_NAMES[mo], _days_in_month(int(y), _MONTH_NAMES[mo])))
        for mo, y in _MONTH_YEAR_RE.findall(lowered)
    ]
    if spans:
        return min(s for s, _ in spans).isoformat(), max(e for _, e in spans).isoformat()

    month_match = _MONTH_RANGE_RE.search(lowered)
    if month_match:
        n = int(month_match.group(1))
        start = _shift_months(today, -n)
        return start.isoformat(), today.isoformat()

    if "this month" in lowered:
        return today.replace(day=1).isoformat(), today.isoformat()

    if "last month" in lowered:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start.isoformat(), last_month_end.isoformat()

    return None


def _shift_months(d: date, months: int) -> date:
    """Shift a date by a whole number of months, clamping the day."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def iso_to_int(iso: str) -> int:
    """'2026-03-01' -> 20260301: Chroma range filters need numbers, not strings."""
    return int(iso.replace("-", ""))


def build_rag_filter(
    category: Optional[str],
    provider: Optional[str],
    date_range: Optional[Tuple[str, str]] = None,
    scope_categories: Optional[List[str]] = None,
) -> Optional[dict]:
    """Chroma `where` for category / provider / period, intersected with an agent's category scope.

    A named out-of-scope category falls back to the whole scope so retrieval stays inside the
    agent's data boundary. The period clause keeps documents whose period overlaps the range;
    documents without period metadata are indexed with an open-ended period (see ChatEngine._rag_metadata_for) so an
    unknown period is never mistaken for a non-match."""
    base: dict = {}
    if category and not (scope_categories and category not in scope_categories):
        base["category"] = category
        if provider:
            base["provider"] = provider
    elif scope_categories:
        base["category"] = {"$in": list(scope_categories)}
    elif provider:
        base["provider"] = provider
    clauses = [{k: v} for k, v in base.items()]  # Chroma rejects multi-key flat filters: one operator per dict
    if date_range:
        start, end = date_range
        clauses += [{"period_end_n": {"$gte": iso_to_int(start)}}, {"period_start_n": {"$lte": iso_to_int(end)}}]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}
