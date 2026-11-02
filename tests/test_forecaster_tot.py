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
UP = [100, 102, 104, 106, 108, 110]
RALLY = [100, 106, 112, 118, 124, 130]
FLAT = [100, 100.2, 100.1, 100.3, 100.2, 100.1]


def card(source_type, sentiment="neutral", confidence=0.8):
    return EvidenceCard(
        source_id=SOURCE[source_type],
        source_type=source_type,
        symbol=None if source_type == "macro" else "SYN",
        title=f"Synthetic {source_type}",
        summary=f"Synthetic {sentiment}",
        retrieved_at=NOW,
        sentiment=sentiment,
        confidence=confidence,
    )


def snap(closes, *cards):
    market = card("market")
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


def mixed():
    """Strong rally vs. moderately negative macro and filings: nothing is decisive at depth 1."""
    return snap(RALLY, card("macro", "negative", 0.4), card("filing", "negative", 0.3))


def report(snapshot, *scenarios, confidence=0.8):
    ids = (snapshot.evidence[0].evidence_id,)
    opinion = lambda s: SimpleNamespace(
        scenario=s, confidence=confidence, evidence_ids=ids
    )  # noqa: E731
    return SimpleNamespace(
        specialists=[SimpleNamespace(opinion=opinion(s)) for s in scenarios]
    )


def chosen(tree):
    return tree.node(tree.selected_id).thought.scenario


def test_unanimous_evidence_converges_at_depth_one_without_llm_calls():
    s = snap(
        UP,
        card("macro", "positive"),
        card("filing", "positive"),
        card("curated", "positive"),
    )
    calls = []
    tree = search(s, critique=lambda: calls.append(1))
    assert tree.termination == "converged" and tree.critic_calls == 0 and calls == []
    assert chosen(tree) == "bullish"


def test_every_scenario_reaches_the_critic_when_depth_one_is_not_decisive():
    s = mixed()
    tree = search(s, critique=lambda: report(s, "base"))
    assert {n.thought.scenario for n in tree.nodes if n.depth == 2} == {
        "bullish",
        "base",
        "bearish",
    }


def test_critics_overturn_the_depth_one_leader():
    s = mixed()
    tree = search(
        s, critique=lambda: report(s, "bearish", "bearish", "bearish", "base")
    )
    leader = min((n for n in tree.nodes if n.depth == 1), key=lambda n: -n.score)
    assert leader.thought.scenario != "bearish"
    assert tree.termination == "depth_limit" and tree.critic_calls == 1
    assert chosen(tree) == "bearish" and tree.node(tree.selected_id).depth == 2


def test_confident_contradicting_critics_hard_prune_a_branch():
    s = mixed()
    tree = search(s, critique=lambda: report(s, "bearish", "bearish", "base"))
    bullish = [
        n for n in tree.nodes if n.depth == 2 and n.thought.scenario == "bullish"
    ]
    assert bullish and all(
        n.status == "pruned" and n.reason == "critics contradict" for n in bullish
    )


def test_beam_keeps_an_opposing_scenario_alive():
    s = snap(
        UP,
        card("macro", "positive"),
        card("filing", "positive"),
        card("curated", "negative", 0.3),
    )
    tree = search(s)
    survivors = [n for n in tree.nodes if n.depth == 1 and n.status != "pruned"]
    assert len({n.thought.scenario for n in survivors}) >= 2


def test_missing_critic_is_visible_and_never_invents_a_review():
    s = mixed()
    tree = search(s, critique=lambda: None)
    depth2 = [n for n in tree.nodes if n.depth == 2]
    assert depth2 and all(
        n.reason == "critic unavailable" and "critic" not in n.components
        for n in depth2
    )


def test_no_evidence_abstains():
    tree = search(
        EvidenceSnapshot(symbol="SYN", as_of=NOW, created_at=NOW, evidence=())
    )
    assert (
        tree.outcome == "abstained"
        and tree.termination == "no_evidence"
        and tree.selected_id is None
    )


def test_offsetting_evidence_selects_base():
    tree = search(snap(FLAT, card("macro", "positive"), card("filing", "negative")))
    assert chosen(tree) == "base"
    assert {"bullish", "bearish", "base"} <= {
        n.thought.scenario for n in tree.nodes if n.depth == 1
    }


def test_search_is_deterministic_bounded_and_scored_in_range():
    s = snap(
        UP,
        card("macro", "negative"),
        card("filing", "positive"),
        card("curated", "negative"),
    )
    a = search(s, critique=lambda: report(s, "base", "bearish", confidence=1.0))
    assert a == search(s, critique=lambda: report(s, "base", "bearish", confidence=1.0))
    assert (
        sum(n.depth == 1 for n in a.nodes) <= 6
        and sum(n.depth == 2 for n in a.nodes) <= 3
    )
    assert all(0 <= n.score <= 1 for n in a.nodes)
