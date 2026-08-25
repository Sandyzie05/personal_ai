"""Cheap-first document category classifier.

Tries keyword heuristics first (free, instant, deterministic). Falls back to
one LLM call only when heuristics don't produce an unambiguous match, so
most uploads never pay for the extra round trip. Always returns a category
key - "other" when nothing matches - so callers never have to special-case
"unknown".
"""

from typing import Optional, Protocol

from .categories import CATEGORIES, DEFAULT_CATEGORY_KEY, category_keys


class ChatCapable(Protocol):
    """Minimal interface classify_with_llm needs - satisfied by OllamaClient."""

    def chat(self, messages, stream: bool = False): ...


def classify_by_keywords(text: str) -> Optional[str]:
    """Return a category key if keyword hits unambiguously favor one category.

    Returns None (rather than "other") when there's no signal or a tie, so
    callers can distinguish "confidently classified" from "give up" and
    decide whether to try the LLM fallback.
    """
    lowered = text.lower()
    scores = {}
    for category in CATEGORIES:
        hits = sum(1 for keyword in category.keywords if keyword in lowered)
        if hits:
            scores[category.key] = hits

    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None  # tie - not confident enough to auto-pick

    return ranked[0][0]


def classify_with_llm(text: str, ollama_client: ChatCapable) -> Optional[str]:
    """Ask the local model to pick a category key when heuristics are unsure.

    Best-effort: any failure (Ollama down, malformed reply) returns None
    rather than raising, so callers can fall back to "other" instead of
    blocking the upload.
    """
    keys = category_keys()
    prompt = (
        "Classify this financial or utility document into exactly one of "
        f"these categories: {', '.join(keys)}.\n\n"
        f"Document excerpt:\n{text[:2000]}\n\n"
        "Respond with only the category key from the list above, nothing else."
    )
    try:
        response = ollama_client.chat(messages=[{"role": "user", "content": prompt}])
        answer = response.get("message", {}).get("content", "").strip().lower()
    except Exception:
        return None

    for key in keys:
        if key in answer:
            return key
    return None


def classify_document(text: str, ollama_client: Optional[ChatCapable] = None) -> str:
    """Best-effort category key for a document; "other" if nothing matches."""
    key = classify_by_keywords(text)
    if key:
        return key

    if ollama_client is not None:
        key = classify_with_llm(text, ollama_client)
        if key:
            return key

    return DEFAULT_CATEGORY_KEY
