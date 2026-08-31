"""Unit tests for src.data_extraction.extractor."""

import pytest

from src.data_extraction.extractor import ExtractionError, extract_structured_data
from src.data_extraction.schemas import ExtractedDocument

VALID_JSON = """
{
  "provider": "Pacific Power",
  "period_start": "2026-05-01",
  "period_end": "2026-05-31",
  "total": 87.42,
  "line_items": [
    {"label": "Electricity usage", "amount": 129.52},
    {"label": "Solar credit", "amount": -42.10}
  ]
}
"""


class FakeOllamaClient:
    def __init__(self, reply=None, raise_error=None):
        self.reply = reply
        self.raise_error = raise_error
        self.last_format = None

    def chat(self, messages, stream=False, format=None):
        self.last_format = format
        if self.raise_error:
            raise self.raise_error
        return {"message": {"content": self.reply}}


def test_extract_structured_data_parses_valid_response():
    client = FakeOllamaClient(reply=VALID_JSON)

    result = extract_structured_data("some bill text", "electricity", client)

    assert isinstance(result, ExtractedDocument)
    assert result.provider == "Pacific Power"
    assert result.total == 87.42
    assert len(result.line_items) == 2
    assert result.line_items[1].label == "Solar credit"
    assert result.line_items[1].amount == -42.10


def test_extract_structured_data_parses_account_identifier():
    reply = """
    {
      "provider": "Chase",
      "account_identifier": "ending in 4412",
      "period_start": "2026-06-01",
      "period_end": "2026-06-30",
      "total": 100.0,
      "line_items": [{"label": "Groceries", "amount": 100.0}]
    }
    """
    client = FakeOllamaClient(reply=reply)

    result = extract_structured_data("some statement text", "credit_card", client)

    assert result.account_identifier == "ending in 4412"


def test_extract_structured_data_account_identifier_defaults_to_none():
    client = FakeOllamaClient(reply=VALID_JSON)

    result = extract_structured_data("some bill text", "electricity", client)

    assert result.account_identifier is None


def test_extract_structured_data_passes_json_schema_format():
    client = FakeOllamaClient(reply=VALID_JSON)

    extract_structured_data("text", "electricity", client)

    assert client.last_format == ExtractedDocument.model_json_schema()


def test_extract_structured_data_returns_none_on_invalid_json():
    client = FakeOllamaClient(reply="not valid json at all")

    assert extract_structured_data("text", "electricity", client) is None


def test_extract_structured_data_raises_extraction_error_on_client_failure():
    client = FakeOllamaClient(raise_error=RuntimeError("ollama unreachable"))

    with pytest.raises(ExtractionError):
        extract_structured_data("text", "electricity", client)
