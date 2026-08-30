"""Unit tests for src.data_extraction.classifier."""

from src.data_extraction.classifier import (
    classify_by_keywords,
    classify_document,
    classify_with_llm,
)


class FakeOllamaClient:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, stream=False, format=None):
        return {"message": {"content": self.reply}}


class RaisingOllamaClient:
    def chat(self, messages, stream=False, format=None):
        raise RuntimeError("ollama unreachable")


class AssertNotCalledClient:
    def chat(self, messages, stream=False, format=None):
        raise AssertionError("LLM fallback should not be called")


def test_classify_by_keywords_electricity():
    text = "Your electric utility bill shows 450 kWh used this month, including a solar credit."

    assert classify_by_keywords(text) == "electricity"


def test_classify_by_keywords_credit_card():
    text = "Statement balance: $1,204.56. Minimum payment due: $35.00. Purchase APR: 24.99%."

    assert classify_by_keywords(text) == "credit_card"


def test_classify_by_keywords_mobile():
    text = (
        "Your T-Mobile wireless bill includes your unlimited plan, data plan usage, "
        "and a monthly recurring charge."
    )

    assert classify_by_keywords(text) == "mobile"


def test_classify_by_keywords_checking_without_literal_account_type_phrase():
    # Real bank statements often never say the literal words "checking
    # account" - just an account summary with opening/closing balances.
    text = "Account summary: opening balance $1,204.56, closing balance $980.12."

    assert classify_by_keywords(text) == "checking"


def test_classify_by_keywords_credit_card_without_literal_statement_phrase():
    text = "New balance: $412.60. Payment due date: 06/15/2026. Available credit: $4,587.40."

    assert classify_by_keywords(text) == "credit_card"


def test_classify_by_keywords_no_signal_returns_none():
    assert classify_by_keywords("Just a random note about lunch plans.") is None


def test_classify_by_keywords_tie_returns_none():
    # One hit each for gas ("therm") and electricity ("kwh") - ambiguous.
    assert classify_by_keywords("therm and kwh both mentioned here") is None


def test_classify_with_llm_returns_matching_key():
    client = FakeOllamaClient("brokerage")

    assert classify_with_llm("some ambiguous text", client) == "brokerage"


def test_classify_with_llm_invalid_reply_returns_none():
    client = FakeOllamaClient("i have no idea what this is")

    assert classify_with_llm("some text", client) is None


def test_classify_with_llm_client_error_returns_none():
    assert classify_with_llm("some text", RaisingOllamaClient()) is None


def test_classify_document_prefers_keywords_over_llm():
    text = "Your natural gas utility bill: 32 therms used, $54.20 total."

    assert classify_document(text, AssertNotCalledClient()) == "gas"


def test_classify_document_falls_back_to_llm_when_keywords_unsure():
    client = FakeOllamaClient("checking")

    assert classify_document("ambiguous financial document text", client) == "checking"


def test_classify_document_falls_back_to_other_when_llm_fails():
    assert classify_document("ambiguous text", RaisingOllamaClient()) == "other"


def test_classify_document_no_client_returns_other_when_keywords_unsure():
    assert classify_document("ambiguous text with no signal") == "other"
