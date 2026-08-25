"""Unit tests for src.interface.file_grouping."""

from src.data_extraction.categories import CATEGORIES, DEFAULT_CATEGORY_KEY
from src.interface.file_grouping import group_files_by_category


def test_files_split_correctly_across_categories():
    files = [
        ("bill1", {"category": "electricity"}),
        ("bill2", {"category": "gas"}),
        ("bill3", {"category": "electricity"}),
    ]

    groups = group_files_by_category(files)
    groups_by_key = {key: entries for key, _label, entries in groups}

    assert [key for key, _ in groups_by_key["electricity"]] == ["bill1", "bill3"]
    assert [key for key, _ in groups_by_key["gas"]] == ["bill2"]


def test_missing_category_falls_back_to_other_and_appears_last():
    files = [
        ("mystery", {}),
        ("power_bill", {"category": "electricity"}),
    ]

    groups = group_files_by_category(files)

    assert [key for key, _label, _entries in groups] == [
        "electricity",
        DEFAULT_CATEGORY_KEY,
    ]
    other_group = groups[-1]
    assert [key for key, _ in other_group[2]] == ["mystery"]


def test_unknown_category_value_falls_back_to_other():
    files = [("weird", {"category": "not_a_real_category"})]

    groups = group_files_by_category(files)

    assert len(groups) == 1
    assert groups[0][0] == DEFAULT_CATEGORY_KEY
    assert [key for key, _ in groups[0][2]] == ["weird"]


def test_empty_category_groups_are_omitted():
    files = [("power_bill", {"category": "electricity"})]

    groups = group_files_by_category(files)

    assert len(groups) == 1
    assert groups[0][0] == "electricity"


def test_group_order_matches_categories_order():
    # Deliberately insert files out of CATEGORIES order.
    files = [
        ("mobile_bill", {"category": "mobile"}),
        ("gas_bill", {"category": "gas"}),
        ("electric_bill", {"category": "electricity"}),
        ("misc", {"category": "other"}),
    ]

    groups = group_files_by_category(files)
    group_keys = [key for key, _label, _entries in groups]

    expected_order = [
        category.key
        for category in CATEGORIES
        if category.key in {"gas", "electricity", "mobile", "other"}
    ]
    assert group_keys == expected_order


def test_no_files_returns_no_groups():
    assert group_files_by_category([]) == []
