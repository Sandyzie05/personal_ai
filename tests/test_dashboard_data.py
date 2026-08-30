"""Unit tests for src.interface.dashboard_data."""

from src.data_extraction.categories import CATEGORIES, DEFAULT_CATEGORY_KEY
from src.interface.dashboard_data import (
    BILL_LIKE_CATEGORY_KEYS,
    count_by_category,
    distinct_vendors,
    filter_records,
    month_over_month_change,
    recent_uploads,
    spend_by_category,
    spend_over_time,
    total_documents,
    total_spend,
)


def _record(
    storage_key,
    category=None,
    total=None,
    period_start=None,
    timestamp=None,
    provider=None,
):
    metadata = {}
    if category is not None:
        metadata["category"] = category
    if timestamp is not None:
        metadata["upload_timestamp"] = timestamp
    if provider is not None:
        metadata["provider"] = provider
    extraction = None
    if total is not None or period_start is not None:
        extraction = {}
        if total is not None:
            extraction["total"] = total
        if period_start is not None:
            extraction["period_start"] = period_start
    return {"storage_key": storage_key, "metadata": metadata, "extraction": extraction}


# --- count_by_category ---------------------------------------------------


def test_count_by_category_empty_input_returns_zero_for_every_category():
    counts = count_by_category([])
    assert len(counts) == len(CATEGORIES)
    assert all(count == 0 for _key, _label, count in counts)


def test_count_by_category_counts_and_includes_zero_categories():
    records = [
        _record("a", category="electricity"),
        _record("b", category="electricity"),
        _record("c", category="gas"),
    ]
    counts = {key: count for key, _label, count in count_by_category(records)}
    assert counts["electricity"] == 2
    assert counts["gas"] == 1
    # zero-count categories still present
    assert counts["brokerage"] == 0
    assert counts["mobile"] == 0
    assert counts["other"] == 0


def test_count_by_category_order_matches_categories():
    keys = [key for key, _label, _count in count_by_category([])]
    assert keys == [category.key for category in CATEGORIES]


def test_count_by_category_missing_or_unknown_category_falls_back_to_other():
    records = [_record("a"), _record("b", category="not_real")]
    counts = {key: count for key, _label, count in count_by_category(records)}
    assert counts[DEFAULT_CATEGORY_KEY] == 2


# --- total_documents -------------------------------------------------------


def test_total_documents_empty():
    assert total_documents([]) == 0


def test_total_documents_counts_all_records_regardless_of_category():
    records = [_record("a", category="electricity"), _record("b", category="other")]
    assert total_documents(records) == 2


# --- total_spend ------------------------------------------------------------


def test_total_spend_empty_input():
    assert total_spend([]) == 0.0


def test_total_spend_sums_bill_like_categories_only():
    records = [
        _record("a", category="electricity", total=100.0),
        _record("b", category="gas", total=50.0),
        _record("c", category="brokerage", total=10000.0),
        _record("d", category="other", total=5.0),
    ]
    assert total_spend(records) == 150.0


def test_total_spend_skips_missing_or_non_numeric_total():
    records = [
        _record("a", category="electricity", total=None),
        _record("b", category="electricity"),
        {
            "storage_key": "c",
            "metadata": {"category": "electricity"},
            "extraction": {"total": "not a number"},
        },
        _record("d", category="electricity", total=25.0),
    ]
    assert total_spend(records) == 25.0


def test_total_spend_respects_custom_category_keys():
    records = [
        _record("a", category="brokerage", total=1000.0),
        _record("b", category="electricity", total=50.0),
    ]
    assert total_spend(records, category_keys={"brokerage"}) == 1000.0


# --- spend_by_category -------------------------------------------------------


def test_spend_by_category_empty_input_returns_zero_for_bill_like_categories():
    result = spend_by_category([])
    assert len(result) == len(BILL_LIKE_CATEGORY_KEYS)
    assert all(total == 0.0 for _label, total in result)


def test_spend_by_category_excludes_brokerage_and_other():
    records = [
        _record("a", category="brokerage", total=1000.0),
        _record("b", category="other", total=5.0),
    ]
    result = spend_by_category(records)
    labels = [label for label, _total in result]
    assert not any("Brokerage" in label for label in labels)
    assert not any("Other" in label for label in labels)
    assert all(total == 0.0 for _label, total in result)


def test_spend_by_category_sums_multiple_records_per_category():
    records = [
        _record("a", category="electricity", total=100.0),
        _record("b", category="electricity", total=25.0),
        _record("c", category="gas", total=40.0),
    ]
    result = dict(spend_by_category(records))
    from src.data_extraction.categories import get_category

    assert result[get_category("electricity").label] == 125.0
    assert result[get_category("gas").label] == 40.0


def test_spend_by_category_order_matches_categories_order():
    result = spend_by_category([])
    labels = [label for label, _total in result]
    expected_labels = [
        category.label
        for category in CATEGORIES
        if category.key in BILL_LIKE_CATEGORY_KEYS
    ]
    assert labels == expected_labels


# --- spend_over_time ----------------------------------------------------------


def test_spend_over_time_empty_input():
    assert spend_over_time([]) == []


def test_spend_over_time_buckets_by_month_and_sorts_ascending():
    records = [
        _record("a", category="electricity", total=100.0, period_start="2026-03-01"),
        _record("b", category="gas", total=50.0, period_start="2026-01-15"),
        _record("c", category="electricity", total=25.0, period_start="2026-01-05"),
    ]
    result = spend_over_time(records)
    assert result == [("2026-01", 75.0), ("2026-03", 100.0)]


def test_spend_over_time_skips_records_without_period_start_or_total():
    records = [
        _record("a", category="electricity", total=100.0),  # no period_start
        _record("b", category="electricity", period_start="2026-01-01"),  # no total
        {
            "storage_key": "c",
            "metadata": {"category": "electricity"},
            "extraction": {"total": 10.0, "period_start": "20"},  # too short
        },
        _record("d", category="brokerage", total=100.0, period_start="2026-02-01"),
    ]
    assert spend_over_time(records) == []


# --- month_over_month_change ---------------------------------------------------


def test_month_over_month_change_empty_input_is_none():
    assert month_over_month_change([]) is None


def test_month_over_month_change_single_month_is_none():
    records = [
        _record("a", category="electricity", total=100.0, period_start="2026-01-05"),
    ]
    assert month_over_month_change(records) is None


def test_month_over_month_change_computes_percent_increase():
    records = [
        _record("a", category="electricity", total=100.0, period_start="2026-01-05"),
        _record("b", category="electricity", total=150.0, period_start="2026-02-05"),
    ]
    assert month_over_month_change(records) == 50.0


def test_month_over_month_change_computes_percent_decrease():
    records = [
        _record("a", category="electricity", total=200.0, period_start="2026-01-05"),
        _record("b", category="electricity", total=50.0, period_start="2026-02-05"),
    ]
    assert month_over_month_change(records) == -75.0


def test_month_over_month_change_uses_latest_two_months_when_more_exist():
    records = [
        _record("a", category="electricity", total=999.0, period_start="2026-01-05"),
        _record("b", category="electricity", total=100.0, period_start="2026-02-05"),
        _record("c", category="electricity", total=150.0, period_start="2026-03-05"),
    ]
    # Only Feb->Mar (100 -> 150) should count; the much larger Jan value is ignored.
    assert month_over_month_change(records) == 50.0


def test_month_over_month_change_none_when_previous_month_total_is_zero():
    records = [
        _record("a", category="electricity", total=0.0, period_start="2026-01-05"),
        _record("b", category="electricity", total=50.0, period_start="2026-02-05"),
    ]
    assert month_over_month_change(records) is None


def test_month_over_month_change_respects_custom_category_keys():
    records = [
        _record("a", category="brokerage", total=1000.0, period_start="2026-01-05"),
        _record("b", category="brokerage", total=1100.0, period_start="2026-02-05"),
    ]
    assert month_over_month_change(records, category_keys={"brokerage"}) == 10.0


# --- recent_uploads -------------------------------------------------------------


def test_recent_uploads_empty_input():
    assert recent_uploads([]) == []


def test_recent_uploads_orders_descending_by_timestamp():
    records = [
        _record("a", timestamp="2026-01-01T00:00:00"),
        _record("b", timestamp="2026-03-01T00:00:00"),
        _record("c", timestamp="2026-02-01T00:00:00"),
    ]
    result = recent_uploads(records, n=5)
    assert [r["storage_key"] for r in result] == ["b", "c", "a"]


def test_recent_uploads_missing_timestamp_sorts_last():
    records = [
        _record("a", timestamp="2026-01-01T00:00:00"),
        _record("b"),  # no timestamp
    ]
    result = recent_uploads(records, n=5)
    assert [r["storage_key"] for r in result] == ["a", "b"]


def test_recent_uploads_n_larger_than_available_records():
    records = [_record("a", timestamp="2026-01-01T00:00:00")]
    result = recent_uploads(records, n=5)
    assert len(result) == 1


def test_recent_uploads_respects_n():
    records = [
        _record("a", timestamp="2026-01-01T00:00:00"),
        _record("b", timestamp="2026-02-01T00:00:00"),
        _record("c", timestamp="2026-03-01T00:00:00"),
    ]
    result = recent_uploads(records, n=2)
    assert [r["storage_key"] for r in result] == ["c", "b"]


# --- distinct_vendors -----------------------------------------------------


def test_distinct_vendors_empty_input():
    assert distinct_vendors([]) == []


def test_distinct_vendors_deduplicates_and_sorts():
    records = [
        _record("a", provider="Rocky Mountain Power"),
        _record("b", provider="Chase"),
        _record("c", provider="Rocky Mountain Power"),
    ]
    assert distinct_vendors(records) == ["Chase", "Rocky Mountain Power"]


def test_distinct_vendors_skips_missing_or_blank_provider():
    records = [
        _record("a"),
        _record("b", provider=""),
        _record("c", provider="   "),
        _record("d", provider="Chase"),
    ]
    assert distinct_vendors(records) == ["Chase"]


# --- filter_records ---------------------------------------------------------


def test_filter_records_no_filters_returns_everything():
    records = [_record("a", category="electricity"), _record("b", category="gas")]
    assert filter_records(records) == records


def test_filter_records_empty_category_or_vendor_list_means_no_filter():
    records = [_record("a", category="electricity"), _record("b", category="gas")]
    assert filter_records(records, category_keys=[], vendors=[]) == records


def test_filter_records_by_category():
    records = [
        _record("a", category="electricity"),
        _record("b", category="gas"),
    ]
    result = filter_records(records, category_keys=["electricity"])
    assert [r["storage_key"] for r in result] == ["a"]


def test_filter_records_by_vendor():
    records = [
        _record("a", category="electricity", provider="Rocky Mountain Power"),
        _record("b", category="electricity", provider="Chase"),
    ]
    result = filter_records(records, vendors=["Chase"])
    assert [r["storage_key"] for r in result] == ["b"]


def test_filter_records_combines_category_and_vendor():
    records = [
        _record("a", category="electricity", provider="Rocky Mountain Power"),
        _record("b", category="gas", provider="Rocky Mountain Power"),
        _record("c", category="electricity", provider="Chase"),
    ]
    result = filter_records(
        records, category_keys=["electricity"], vendors=["Rocky Mountain Power"]
    )
    assert [r["storage_key"] for r in result] == ["a"]
