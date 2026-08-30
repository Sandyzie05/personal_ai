"""Unit tests for src.ai_engine.query_analysis."""

from datetime import date

from src.ai_engine.query_analysis import (
    detect_category_from_query,
    detect_provider_from_query,
    parse_relative_date_range,
)


def test_detect_category_solar_credit_maps_to_electricity():
    assert (
        detect_category_from_query("How much solar credit did I get?") == "electricity"
    )


def test_detect_category_robinhood_maps_to_brokerage():
    assert detect_category_from_query("What's my Robinhood balance?") == "brokerage"


def test_detect_category_no_match_returns_none():
    assert detect_category_from_query("What's the weather like today?") is None


def test_detect_category_ambiguous_mention_of_two_returns_none():
    query = "Compare my electricity bill and my gas bill this month"
    assert detect_category_from_query(query) is None


def test_detect_category_tmobile_maps_to_mobile():
    query = "summarize the tmobile bill for the month of August 2026"
    assert detect_category_from_query(query) == "mobile"


def test_detect_category_verizon_maps_to_mobile():
    assert detect_category_from_query("what did I pay verizon last month") == "mobile"


def test_parse_last_n_months():
    today = date(2026, 8, 24)

    result = parse_relative_date_range(
        "how much did I spend in the last 3 months", today=today
    )

    assert result == ("2026-05-24", "2026-08-24")


def test_parse_last_n_months_clamps_across_year_boundary():
    today = date(2026, 1, 15)

    result = parse_relative_date_range("last 2 months", today=today)

    assert result == ("2025-11-15", "2026-01-15")


def test_parse_this_month():
    today = date(2026, 8, 24)

    result = parse_relative_date_range("what did I spend this month", today=today)

    assert result == ("2026-08-01", "2026-08-24")


def test_parse_last_month():
    today = date(2026, 8, 5)

    result = parse_relative_date_range("what was my bill last month", today=today)

    assert result == ("2026-07-01", "2026-07-31")


def test_parse_last_month_handles_january():
    today = date(2026, 1, 10)

    result = parse_relative_date_range("last month's total", today=today)

    assert result == ("2025-12-01", "2025-12-31")


def test_parse_no_recognizable_range_returns_none():
    assert parse_relative_date_range("how much did I ever spend on electricity") is None


def test_parse_explicit_month_and_year():
    result = parse_relative_date_range(
        "summarize the tmobile bill for the month of August 2026"
    )
    assert result == ("2026-08-01", "2026-08-31")


def test_parse_explicit_month_and_year_clamps_to_month_length():
    result = parse_relative_date_range("robinhood summary for February 2024")
    assert result == ("2024-02-01", "2024-02-29")


def test_detect_provider_single_match():
    result = detect_provider_from_query(
        "what did I pay on my Chase card", ["Chase", "Capital One"]
    )
    assert result == "Chase"


def test_detect_provider_no_match_returns_none():
    result = detect_provider_from_query(
        "what did I spend last month", ["Chase", "Capital One"]
    )
    assert result is None


def test_detect_provider_ambiguous_mention_of_two_returns_none():
    result = detect_provider_from_query(
        "compare my Chase card and Capital One card", ["Chase", "Capital One"]
    )
    assert result is None
