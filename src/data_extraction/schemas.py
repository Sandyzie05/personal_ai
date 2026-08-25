"""Generic structured-extraction schema shared by every document category.

One schema for all providers/categories (electricity, gas, credit card,
checking, brokerage, ...) instead of a bespoke Pydantic model per vendor -
the LLM adapts to each provider's layout; the schema just needs to be
generic enough to hold whatever it finds.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    label: str
    amount: float


class ExtractedDocument(BaseModel):
    """Structured facts pulled from a bill/statement, regardless of provider.

    Credits (e.g. a solar credit) are represented as negative amounts so a
    plain sum over line_items gives the net total.
    """

    provider: Optional[str] = None
    period_start: Optional[str] = None  # ISO YYYY-MM-DD when determinable
    period_end: Optional[str] = None
    total: Optional[float] = None
    line_items: List[LineItem] = Field(default_factory=list)
