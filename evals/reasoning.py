"""ToT thesis-selection eval (docs/reasoning_design.md §7). Synthetic snapshots; labels are the designer's
stated correct reading of each evidence+critic configuration, so this is a regression/ablation check of the
search policy, not market accuracy. Baselines: B-linear commits to the best depth-1 thesis (chain-of-thought
style, no critics); B-vote takes the critics' majority scenario (no heuristic evidence)."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.forecaster.models import EvidenceCard, EvidenceSnapshot, MarketData, PriceBar
from src.forecaster.tot import search

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
SOURCE = {
    "market": "yahoo-chart",
    "macro": "fred",
    "filing": "sec-edgar",
    "curated": "curated-ai-infrastructure-theme",
}
UP, DOWN = [100, 102, 104, 106, 108, 110], [110, 108, 106, 104, 102, 100]
RALLY, FLAT = [100, 106, 112, 118, 124, 130], [100, 100.2, 100.1, 100.3, 100.2, 100.1]


def _card(kind, sentiment="neutral", confidence=0.8):
    return EvidenceCard(
        source_id=SOURCE[kind],
        source_type=kind,
        symbol=None if kind == "macro" else "SYN",
        title=f"Synthetic {kind}",
        summary=f"Synthetic {sentiment}",
        retrieved_at=NOW,
        sentiment=sentiment,
        confidence=confidence,
    )


def _snap(closes, *cards):
    market = _card("market")
    bars = tuple(
        PriceBar(
            symbol="SYN",
            session=date(2026, 10, 1) + timedelta(days=i),
            close=c,
            source_id="yahoo-chart",
            retrieved_at=NOW,
        )
        for i, c in enumerate(closes)
    )
    data = MarketData(
        symbol="SYN",
        exchange="XNAS",
        currency="USD",
        bars=bars,
        price_convention="unadjusted_close",
        evidence=(market,),
    )
    return EvidenceSnapshot(
        symbol="SYN", as_of=NOW, created_at=NOW, market=data, evidence=(market, *cards)
    )


def _critics(snapshot, scenarios):
    if scenarios is None:
        return None
    ids = (snapshot.evidence[0].evidence_id,)
    return SimpleNamespace(
        specialists=[
            SimpleNamespace(
                opinion=SimpleNamespace(scenario=s, confidence=0.8, evidence_ids=ids)
            )
            for s in scenarios
        ]
    )


def _mixed():
    return _snap(
        RALLY, _card("macro", "negative", 0.4), _card("filing", "negative", 0.3)
    )


# (id, snapshot, critic scenarios or None, expected scenario or "abstain")
CASES = [
    (
        "unanimous-bull",
        _snap(
            UP,
            _card("macro", "positive"),
            _card("filing", "positive"),
            _card("curated", "positive"),
        ),
        ["bullish"] * 4,
        "bullish",
    ),
    (
        "unanimous-bear",
        _snap(
            DOWN,
            _card("macro", "negative"),
            _card("filing", "negative"),
            _card("curated", "negative"),
        ),
        ["bearish"] * 4,
        "bearish",
    ),
    (
        "rally-vs-fundamentals, critics bearish",
        _mixed(),
        ["bearish", "bearish", "bearish", "base"],
        "bearish",
    ),
    (
        "rally-vs-fundamentals, critics bullish",
        _mixed(),
        ["bullish", "bullish", "bullish", "base"],
        "bullish",
    ),
    (
        "rally-vs-fundamentals, critics base",
        _mixed(),
        ["base", "base", "base", "bearish"],
        "base",
    ),
    (
        "offsetting signals",
        _snap(FLAT, _card("macro", "positive"), _card("filing", "negative")),
        ["bullish", "bearish"],
        "base",
    ),
    (
        "strong evidence, one contrarian critic",
        _snap(
            UP,
            _card("macro", "positive"),
            _card("filing", "positive"),
            _card("curated", "positive"),
        ),
        ["bearish", "bullish", "bullish"],
        "bullish",
    ),
    (
        "no evidence",
        EvidenceSnapshot(symbol="SYN", as_of=NOW, created_at=NOW, evidence=()),
        None,
        "abstain",
    ),
]


def run_reasoning():
    rows, tot_ok, linear_ok, vote_ok, calls, nodes_max = [], 0, 0, 0, 0, 0
    for cid, snap, critics, want in CASES:
        tree = search(snap, critique=lambda s=snap, c=critics: _critics(s, c))
        got = (
            tree.node(tree.selected_id).thought.scenario
            if tree.selected_id
            else "abstain"
        )
        depth1 = sorted(
            (n for n in tree.nodes if n.depth == 1),
            key=lambda n: (-n.score, n.thought.scenario),
        )
        linear = depth1[0].thought.scenario if depth1 else "abstain"
        vote = Counter(critics).most_common(1)[0][0] if critics else "abstain"
        tot_ok, linear_ok, vote_ok = (
            tot_ok + (got == want),
            linear_ok + (linear == want),
            vote_ok + (vote == want),
        )
        calls, nodes_max = calls + tree.critic_calls, max(nodes_max, len(tree.nodes))
        rows.append((cid, want, got, linear, vote, tree.termination))
    return {
        "n": len(CASES),
        "tot": tot_ok,
        "linear": linear_ok,
        "vote": vote_ok,
        "critic_calls": calls,
        "nodes_max": nodes_max,
        "cases": rows,
    }
