"""Unit tests for src.data_extraction.categories."""

from src.data_extraction.categories import (
    CATEGORIES,
    DEFAULT_CATEGORY_KEY,
    category_keys,
    get_category,
)


def test_category_keys_are_unique():
    keys = category_keys()

    assert len(keys) == len(set(keys))


def test_get_category_known_key():
    category = get_category("electricity")

    assert category.key == "electricity"
    assert any(f.key == "provider" for f in category.fields)


def test_get_category_unknown_key_falls_back_to_other():
    category = get_category("nonexistent")

    assert category.key == DEFAULT_CATEGORY_KEY


def test_every_category_except_other_has_a_provider_field():
    for category in CATEGORIES:
        if category.key == "other":
            continue
        assert any(f.key == "provider" for f in category.fields), category.key
