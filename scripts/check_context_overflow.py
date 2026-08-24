#!/usr/bin/env python3
"""
Context-overflow guard for the Personal AI System.

Run via `make check-context` (optionally `make check-context FILE=path/to/text.txt`).

This exists so both a human and an AI coding agent can cheaply sanity-check,
*before* running the full app, whether:

  1. The context window this app assumes (src.config.DEFAULT_CONTEXT_WINDOW_TOKENS)
     is larger than what Ollama actually reports for the configured chat model.
     That mismatch is exactly the kind of bug that causes silent truncation or a
     hard error deep inside a chat request instead of a clear message up front.
  2. A specific piece of text (e.g. a big document you're about to add to the
     vault, or a prompt you're hand-crafting) would overflow the RAG context
     budget that ChatEngine._fit_context_to_budget() enforces at runtime.

Uses a coarse chars-per-token heuristic (src.config.CHARS_PER_TOKEN_ESTIMATE)
rather than a real tokenizer, matching what the running app uses - good enough
to catch gross overflows cheaply and offline, not meant to be byte-exact.
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    CHARS_PER_TOKEN_ESTIMATE,
)

# Mirrors ChatEngine.RESERVED_TOKENS - keep in sync with src/ai_engine/chat_engine.py.
RESERVED_TOKENS = 1024


def estimate_tokens(text: str) -> int:
    return max(len(text) // CHARS_PER_TOKEN_ESTIMATE, 1)


def get_model_context_window(model: str) -> Optional[int]:
    """Best-effort lookup of the real context window via `ollama show`."""
    try:
        result = subprocess.run(
            ["ollama", "show", model], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("context length"):
            for token in stripped.split():
                if token.isdigit():
                    return int(token)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model", default=DEFAULT_CHAT_MODEL, help="Chat model to check against"
    )
    parser.add_argument(
        "--file", help="Text file to estimate against the RAG context budget"
    )
    args = parser.parse_args()

    exit_code = 0

    print(f"Configured chat model: {args.model}")
    print(
        f"App-assumed context window: {DEFAULT_CONTEXT_WINDOW_TOKENS} tokens "
        f"(~{DEFAULT_CONTEXT_WINDOW_TOKENS * CHARS_PER_TOKEN_ESTIMATE} chars)"
    )

    real_window = get_model_context_window(args.model)
    if real_window is None:
        print(
            "⚠️  Could not determine the model's real context window "
            "(is Ollama running and is the model pulled?). Skipping window check."
        )
    else:
        print(f"Ollama-reported context window for {args.model}: {real_window} tokens")
        if DEFAULT_CONTEXT_WINDOW_TOKENS > real_window:
            print(
                f"❌ App assumes a larger context window ({DEFAULT_CONTEXT_WINDOW_TOKENS}) "
                f"than the model actually has ({real_window}). Set "
                f"PERSONAL_AI_CONTEXT_WINDOW_TOKENS={real_window} (or lower) to avoid "
                f"silent truncation/errors from Ollama."
            )
            exit_code = 1
        else:
            print(
                "✅ App's context-window assumption is within the model's real limit."
            )

    text = None
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()

    if text:
        tokens = estimate_tokens(text)
        budget = max(DEFAULT_CONTEXT_WINDOW_TOKENS - RESERVED_TOKENS, 512)
        print(f"\nInput text: ~{tokens} tokens ({len(text)} chars)")
        if tokens > budget:
            print(
                f"❌ This text alone (~{tokens} tokens) would overflow the "
                f"{budget}-token RAG context budget and get truncated before "
                f"reaching the model."
            )
            exit_code = 1
        else:
            print(f"✅ Fits within the {budget}-token RAG context budget.")
    else:
        print("\n(no --file given and no stdin piped - skipping text-size check)")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
