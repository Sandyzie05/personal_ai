"""LLM-based structured extraction: one prompt + schema for any provider.

Replaces the old per-vendor regex approach - scales to new providers or
categories by relying on the model to adapt to layout differences instead
of writing new pattern-matching code per vendor.
"""

import logging
from typing import Optional, Protocol

from ..config import DEFAULT_EXTRACTION_TEXT_CHARS
from .schemas import ExtractedDocument

logger = logging.getLogger(__name__)


class ChatCapable(Protocol):
    def chat(self, messages, stream: bool = False, format=None): ...


class ExtractionError(Exception):
    """Raised when the extraction call itself fails (e.g. Ollama unreachable)."""


def extract_structured_data(
    text: str, category: str, ollama_client: ChatCapable
) -> Optional[ExtractedDocument]:
    """Extract structured line items/totals from document text via the local LLM.

    Returns None (rather than raising) when the model's reply doesn't parse
    as valid structured data - callers treat that as "no structured data
    available, the raw text is still searchable via RAG" rather than an
    upload failure.
    """
    prompt = (
        f"This is a {category} bill or statement. Extract the provider name, "
        "an account identifier if printed on the document (e.g. the last 4 "
        "digits of the account/card number, or 'ending in 1234' - null if "
        "not present), the statement period (as YYYY-MM-DD dates if "
        "determinable), the total amount due/balance as printed on the "
        "document (a positive number - do not compute it yourself from the "
        "line items), and every individual line item with its label and "
        "amount. Represent credits and discounts as negative line item "
        "amounts (e.g. a $42.10 solar credit is a line item of -42.10).\n\n"
        f"Document text:\n{text[:DEFAULT_EXTRACTION_TEXT_CHARS]}"
    )
    try:
        response = ollama_client.chat(
            messages=[{"role": "user", "content": prompt}],
            format=ExtractedDocument.model_json_schema(),
        )
    except Exception as e:
        raise ExtractionError(f"Extraction failed: {e}") from e

    content = response.get("message", {}).get("content", "")
    try:
        return ExtractedDocument.model_validate_json(content)
    except Exception:
        logger.warning("Structured extraction returned invalid JSON, skipping")
        return None
