"""Pure helpers for aggregating vault records for the Dashboard page.

Kept separate from `main.py`/`dashboard.py` (and free of any Streamlit /
vault imports) so the aggregation logic can be unit tested without mocking
Streamlit or a real vault backend - same pattern as `file_grouping.py`.

Every function here consumes `records: List[Dict[str, Any]]` where each
record has the shape `{"storage_key": str, "metadata": Dict, "extraction":
Optional[Dict]}`, matching what `render_files_page()` already builds.
"""

from typing import Any, Dict, List, Optional, Tuple

from src.data_extraction.categories import (
    CATEGORIES,
    DEFAULT_CATEGORY_KEY,
)

# Categories whose extraction["total"] represents money owed/spent.
# Deliberately excludes "brokerage" (its total is portfolio value, not
# spend) and "other" (no consistent meaning for total).
BILL_LIKE_CATEGORY_KEYS = {"electricity", "gas", "credit_card", "checking", "mobile"}


def _record_category_key(record: Dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    category_key = metadata.get("category") or DEFAULT_CATEGORY_KEY
    if category_key not in {category.key for category in CATEGORIES}:
        category_key = DEFAULT_CATEGORY_KEY
    return category_key


def count_by_category(records: List[Dict[str, Any]]) -> List[Tuple[str, str, int]]:
    """Count records per category, for EVERY category (including zero-count)."""
    counts: Dict[str, int] = {category.key: 0 for category in CATEGORIES}

    for record in records:
        counts[_record_category_key(record)] += 1

    return [
        (category.key, category.label, counts[category.key]) for category in CATEGORIES
    ]


def total_documents(records: List[Dict[str, Any]]) -> int:
    return len(records)


def _numeric_total(extraction: Optional[Dict[str, Any]]) -> Optional[float]:
    if not extraction:
        return None
    total = extraction.get("total")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None
    return float(total)


def total_spend(
    records: List[Dict[str, Any]], category_keys: Optional[set] = None
) -> float:
    """Sum extraction["total"] for records in `category_keys` (default bill-like)."""
    keys = category_keys if category_keys is not None else BILL_LIKE_CATEGORY_KEYS

    total = 0.0
    for record in records:
        if _record_category_key(record) not in keys:
            continue
        amount = _numeric_total(record.get("extraction"))
        if amount is None:
            continue
        total += amount

    return total


def spend_by_category(
    records: List[Dict[str, Any]], category_keys: Optional[set] = None
) -> List[Tuple[str, float]]:
    """Total spend per category in `category_keys` (default bill-like), in
    `CATEGORIES` order; 0.0 for categories with no matching records."""
    keys = category_keys if category_keys is not None else BILL_LIKE_CATEGORY_KEYS

    totals: Dict[str, float] = {key: 0.0 for key in keys}
    for record in records:
        category_key = _record_category_key(record)
        if category_key not in keys:
            continue
        amount = _numeric_total(record.get("extraction"))
        if amount is None:
            continue
        totals[category_key] += amount

    return [
        (category.label, totals[category.key])
        for category in CATEGORIES
        if category.key in keys
    ]


def spend_over_time(
    records: List[Dict[str, Any]], category_keys: Optional[set] = None
) -> List[Tuple[str, float]]:
    """Bucket spend by `extraction["period_start"][:7]` ("YYYY-MM"), summing
    `total` for records in `category_keys` (default bill-like) that have
    both a numeric total and a period_start string of at least 7 chars."""
    keys = category_keys if category_keys is not None else BILL_LIKE_CATEGORY_KEYS

    buckets: Dict[str, float] = {}
    for record in records:
        if _record_category_key(record) not in keys:
            continue
        extraction = record.get("extraction")
        amount = _numeric_total(extraction)
        if amount is None:
            continue
        period_start = (extraction or {}).get("period_start")
        if not isinstance(period_start, str) or len(period_start) < 7:
            continue
        month_key = period_start[:7]
        buckets[month_key] = buckets.get(month_key, 0.0) + amount

    return sorted(buckets.items(), key=lambda item: item[0])


def recent_uploads(records: List[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """The `n` records with the most recent `metadata.upload_timestamp`,
    descending; records missing a timestamp sort last."""

    def sort_key(record: Dict[str, Any]) -> str:
        return (record.get("metadata") or {}).get("upload_timestamp", "")

    ordered = sorted(records, key=sort_key, reverse=True)
    return ordered[:n]
