"""Document category registry.

Adding support for a new provider or document type means adding an entry
here - not writing a new parser/extractor class. The classifier
(`classifier.py`) and the Upload page form are both driven by this list, so
one new `Category` entry gets you category detection, a metadata form, and
inclusion in structured-extraction/query filtering all at once.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class CategoryField:
    """One metadata field shown on the Upload page form for a category."""

    key: str
    label: str
    required: bool = False


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    # Lowercase keywords the heuristic classifier looks for in document text.
    keywords: List[str] = field(default_factory=list)
    fields: List[CategoryField] = field(default_factory=list)


CATEGORIES: List[Category] = [
    Category(
        key="electricity",
        label="⚡ Electricity",
        keywords=[
            "kwh",
            "kilowatt",
            "electric utility",
            "solar credit",
            "net metering",
        ],
        fields=[
            CategoryField("provider", "Utility provider", required=True),
            CategoryField("account_label", "Account nickname or last 4 digits"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="gas",
        label="🔥 Gas",
        keywords=["therm", "natural gas", "gas utility", "ccf"],
        fields=[
            CategoryField("provider", "Utility provider", required=True),
            CategoryField("account_label", "Account nickname or last 4 digits"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="credit_card",
        label="💳 Credit Card",
        keywords=[
            "statement balance",
            "minimum payment",
            "credit limit",
            "purchase apr",
        ],
        fields=[
            CategoryField("provider", "Card issuer", required=True),
            CategoryField("account_label", "Card nickname or last 4 digits"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="checking",
        label="🏦 Checking / Savings",
        keywords=[
            "checking account",
            "savings account",
            "beginning balance",
            "ending balance",
        ],
        fields=[
            CategoryField("provider", "Bank name", required=True),
            CategoryField("account_label", "Account nickname or last 4 digits"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="brokerage",
        label="📈 Brokerage",
        keywords=[
            "robinhood",
            "e*trade",
            "etrade",
            "portfolio value",
            "dividends",
            "shares",
        ],
        fields=[
            CategoryField(
                "provider", "Brokerage (e.g. Robinhood, E*TRADE)", required=True
            ),
            CategoryField("account_label", "Account nickname or last 4 digits"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="mobile",
        label="📱 Mobile / Phone",
        keywords=[
            "t-mobile",
            "verizon",
            "at&t",
            "wireless",
            "data plan",
            "monthly recurring charge",
            "talk, text",
            "sim card",
            "unlimited plan",
        ],
        fields=[
            CategoryField(
                "provider", "Carrier (e.g. T-Mobile, Verizon, AT&T)", required=True
            ),
            CategoryField("account_label", "Phone number or account nickname"),
            CategoryField("period_start", "Statement period start (YYYY-MM-DD)"),
            CategoryField("period_end", "Statement period end (YYYY-MM-DD)"),
        ],
    ),
    Category(
        key="other",
        label="📄 Other",
        keywords=[],
        fields=[
            CategoryField("note", "What is this document?"),
        ],
    ),
]

CATEGORY_BY_KEY = {category.key: category for category in CATEGORIES}

DEFAULT_CATEGORY_KEY = "other"


def get_category(key: str) -> Category:
    """Look up a category by key, falling back to "other" for unknown keys."""
    return CATEGORY_BY_KEY.get(key, CATEGORY_BY_KEY[DEFAULT_CATEGORY_KEY])


def category_keys() -> List[str]:
    return [category.key for category in CATEGORIES]
