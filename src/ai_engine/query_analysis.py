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
from typing import Optional, Tuple

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

    month_year_match = _MONTH_YEAR_RE.search(lowered)
    if month_year_match:
        month = _MONTH_NAMES[month_year_match.group(1).lower()]
        year = int(month_year_match.group(2))
        start = date(year, month, 1)
        end = date(year, month, _days_in_month(year, month))
        return start.isoformat(), end.isoformat()

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
