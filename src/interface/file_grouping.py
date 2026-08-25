"""Pure helpers for grouping uploaded vault files by category.

Kept separate from `main.py` (and free of any Streamlit / vault imports) so
the grouping logic can be unit tested without mocking Streamlit or a real
vault backend.
"""

from typing import Any, Dict, List, Tuple

from src.data_extraction.categories import (
    CATEGORIES,
    DEFAULT_CATEGORY_KEY,
)

FileEntry = Tuple[str, Dict[str, Any]]
CategoryGroup = Tuple[str, str, List[FileEntry]]


def group_files_by_category(files: List[FileEntry]) -> List[CategoryGroup]:
    """Group `(key, metadata)` file entries into per-category buckets.

    Groups are ordered to match `CATEGORIES` (so "other"/uncategorized
    always comes last), a missing or unrecognized `category` metadata value
    falls back to `DEFAULT_CATEGORY_KEY`, and categories with no files are
    omitted entirely.
    """
    buckets: Dict[str, List[FileEntry]] = {category.key: [] for category in CATEGORIES}

    for key, metadata in files:
        category_key = (metadata or {}).get("category") or DEFAULT_CATEGORY_KEY
        if category_key not in buckets:
            category_key = DEFAULT_CATEGORY_KEY
        buckets[category_key].append((key, metadata))

    groups: List[CategoryGroup] = []
    for category in CATEGORIES:
        entries = buckets[category.key]
        if not entries:
            continue
        groups.append((category.key, category.label, entries))

    return groups
