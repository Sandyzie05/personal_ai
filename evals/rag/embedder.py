"""Deterministic offline stand-in for the embedding model: signed hashed bag-of-words, L2-normalised.
It is NOT semantic; it only lets the eval exercise chunking/filters/fusion/assembly/grounding with no server.
Absolute numbers therefore do not transfer to the real oMLX model: use them for A/B deltas and regressions."""

import hashlib
import math
import re
from typing import List

STOP = {"the", "a", "an", "of", "on", "my", "for", "in", "is", "was", "what", "how", "much", "do", "i", "to", "and",
        "or", "did", "me", "your", "from", "through", "than", "at", "it", "be", "are", "this", "that"}
DIM = 1024
WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> List[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


class HashEmbedClient:
    model = "hash-bow-eval"

    def __init__(self):
        self.calls = 0  # texts embedded
        self.http_calls = 0  # batches

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        self.http_calls += 1
        return [self.generate_embedding(t) for t in texts]

    def generate_embedding(self, text: str) -> List[float]:
        self.calls += 1
        v = [0.0] * DIM
        for w in tokens(text):
            h = int(hashlib.md5(w.encode()).hexdigest()[:8], 16)
            v[h % DIM] += 1.0 if (h >> 16) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
