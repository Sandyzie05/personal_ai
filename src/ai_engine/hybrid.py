"""In-memory lexical side of hybrid retrieval: BM25, reciprocal-rank fusion, IDF-weighted query coverage.

Deliberately memory-only (docs/rag_design.md S-R3): chunk text is tokenised from the live index each session, never
written to a second on-disk index."""

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "on", "my", "for", "in", "is", "was", "what", "how", "much", "do", "i", "to", "and",
         "or", "did", "me", "your", "from", "through", "than", "at", "it", "be", "are", "this", "that", "when", "does",
         "many", "which", "who", "last", "year", "month", "have", "has", "with", "can", "you"}
# Words people use for "a bill" or for comparing/aggregating that the bill itself need not contain; ignored for
# coverage only.
_GENERIC = {"bill", "bills", "statement", "statements", "pay", "paid", "owe", "cost", "spend", "spent", "amount",
            # words that state the intent of a comparison, not content a document contains
            "higher", "lower", "more", "less", "bigger", "smaller", "compare", "compared", "difference", "change",
            "changed", "increase", "increased", "decrease", "decreased", "average", "usual", "typical"}
# Month names and years are consumed by the period filter (query_analysis); bills print dates as digits.
_TEMPORAL = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december"}


def tokenize(text: str) -> List[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def query_terms(query: str) -> List[str]:
    """Distinctive query terms for coverage: no stopwords, month names or 4-digit years."""
    return [t for t in tokenize(query) if t not in _TEMPORAL and t not in _GENERIC and not re.fullmatch(r"(19|20)\d\d", t)]


def rrf(rankings: Sequence[Sequence[str]], k: int = 60) -> Dict[str, float]:
    """Reciprocal rank fusion: id -> sum 1/(k+rank)."""
    out: Dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            out[cid] = out.get(cid, 0.0) + 1.0 / (k + rank)
    return out


class LexicalIndex:
    def __init__(self, ids: Sequence[str], docs: Sequence[str]):
        self.tokens: Dict[str, Counter] = {i: Counter(tokenize(d)) for i, d in zip(ids, docs)}
        self.len = {i: sum(c.values()) for i, c in self.tokens.items()}
        self.n = max(len(self.tokens), 1)
        self.avgdl = (sum(self.len.values()) / self.n) or 1.0
        self.df: Counter = Counter()
        for c in self.tokens.values():
            self.df.update(c.keys())

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)  # unseen term -> maximal idf: a query word nothing contains is strong evidence
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, allowed: Optional[Set[str]] = None, n: int = 20) -> List[str]:
        terms = set(tokenize(query)) - _TEMPORAL
        scored = []
        for cid, tf in self.tokens.items():
            if allowed is not None and cid not in allowed:
                continue
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self.idf(t) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.len[cid] / self.avgdl))
            if s > 0:
                scored.append((-s, cid))
        scored.sort()
        return [cid for _, cid in scored[:n]]

    def coverage(self, query: str, cid: str) -> float:
        """IDF-weighted share of the query's distinctive terms present in the chunk (1.0 if it has none)."""
        terms = set(query_terms(query))
        total = sum(self.idf(t) for t in terms)
        if not total:
            return 1.0
        have = self.tokens.get(cid, Counter())
        return sum(self.idf(t) for t in terms if t in have) / total
