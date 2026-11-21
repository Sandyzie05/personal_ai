"""P8: parametric synthetic ToT cases with a planted label (docs/forecaster.md Part 2; roadmap P8).

`make_case(seed)` plants a scenario by construction, then draws evidence around it:
- trend: drift = direction * U(0.02, 0.10) over 6 closes, plus Gaussian noise (sigma 0.6% of price per step);
- each of 3 families (macro, filing, curated): signal = direction * U(0.3, 0.9) + N(0, SIGMA); `contradict` of them are
  sign-flipped; |signal| < 0.15 reads neutral. A planted "base" case has direction 0 (signals are pure noise);
- critics: 4 opinions, each correct with p=CRITIC_ACC, else a uniformly random other scenario; confidence U(0.4, 0.9);
  10% of cases have no critic report (outage).
The label is the planted scenario, never a reading of the system's output.

Split. Seeds 0-99 = dev (all tuning). 100-199 was the first held-out range: with raw normalised scores ToT beat both
baselines on accuracy but lost to critic-vote on Brier (0.506 vs 0.457) because normalised node scores are
under-confident. That range is now BURNED (reported, never gated). Each method then got a sharpening exponent
p ~ score^k fitted on dev only (integer grid 1..20), and the gated held-out range is the fresh 200-299.
"""

import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.forecaster.models import EvidenceCard, EvidenceSnapshot, MarketData, PriceBar
from src.forecaster.tot import search

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
SCENARIOS = ("bullish", "base", "bearish")
DIRECTION = {"bullish": 1, "base": 0, "bearish": -1}
SOURCE = {
    "market": "yahoo-chart",
    "macro": "fred",
    "filing": "sec-edgar",
    "curated": "curated-ai-infrastructure-theme",
}
SIGMA, CRITIC_ACC, OUTAGE = 0.35, 0.6, 0.1
DEV, BURNED, HELD_OUT = range(0, 100), range(100, 200), range(200, 300)
# Dev-fitted (2026-11-21), frozen before HELD_OUT was evaluated. Same treatment for every method.
CALIBRATION_K = {"tot": 7, "linear": 2, "vote": 5}


def _card(kind, sentiment, confidence, i):
    return EvidenceCard(
        source_id=SOURCE[kind],
        source_type=kind,
        symbol=None if kind == "macro" else "SYN",
        title=f"Synthetic {kind} {i}",
        summary=f"Synthetic {sentiment}",
        retrieved_at=NOW,
        sentiment=sentiment,
        confidence=round(confidence, 3),
    )


def make_case(seed: int):
    """-> (EvidenceSnapshot, critic_report | None, label)."""
    rng = random.Random(seed)
    label = SCENARIOS[seed % 3]
    d = DIRECTION[label]
    drift = d * rng.uniform(0.02, 0.10)
    closes, price = [], 100.0
    for _ in range(6):
        closes.append(round(price, 4))
        price *= 1 + drift / 5 + rng.gauss(0, 0.006)
    contradict = rng.choice((0, 0, 1, 1, 2)) if d else 0
    flipped = set(rng.sample(range(3), contradict))
    cards = []
    for i, kind in enumerate(("macro", "filing", "curated")):
        signal = d * rng.uniform(0.3, 0.9) * (-1 if i in flipped else 1) + rng.gauss(
            0, SIGMA
        )
        sentiment = (
            "positive" if signal > 0.15 else "negative" if signal < -0.15 else "neutral"
        )
        cards.append(_card(kind, sentiment, min(1.0, 0.3 + abs(signal)), seed))
    market = _card("market", "neutral", 0.8, seed)
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
    snap = EvidenceSnapshot(
        symbol="SYN", as_of=NOW, created_at=NOW, market=data, evidence=(market, *cards)
    )
    if rng.random() < OUTAGE:
        return snap, None, label
    ids = [c.evidence_id for c in snap.evidence]
    opinions = []
    for _ in range(4):
        s = (
            label
            if rng.random() < CRITIC_ACC
            else rng.choice([x for x in SCENARIOS if x != label])
        )
        opinions.append(
            SimpleNamespace(
                opinion=SimpleNamespace(
                    scenario=s,
                    confidence=rng.uniform(0.4, 0.9),
                    evidence_ids=(rng.choice(ids),),
                )
            )
        )
    return snap, SimpleNamespace(specialists=opinions), label


def _probs(scores: dict) -> dict:
    total = sum(scores.values())
    return (
        {s: scores.get(s, 0.0) / total for s in SCENARIOS}
        if total
        else {s: 1 / 3 for s in SCENARIOS}
    )


def calibrate(p: dict, k: float) -> dict:
    q = {s: p[s] ** k for s in SCENARIOS}
    total = sum(q.values())
    return {s: q[s] / total for s in SCENARIOS} if total else p


def _brier(p: dict, label: str) -> float:
    return sum((p[s] - (s == label)) ** 2 for s in SCENARIOS)


def _best_by_scenario(nodes) -> dict:
    best = defaultdict(float)
    for n in nodes:
        best[n.thought.scenario] = max(best[n.thought.scenario], n.score)
    return dict(best)


def predict(seed: int):
    """Per method: (choice | 'abstain', probability dict). ToT/linear p = node scores normalised over scenarios;
    critic-vote p = confidence-weighted vote share. Abstain = uniform."""
    snap, report, label = make_case(seed)
    tree = search(snap, critique=lambda: report)
    final_depth = max((n.depth for n in tree.nodes), default=1)
    final = [n for n in tree.nodes if n.depth == final_depth and n.status != "pruned"]
    tot_choice = (
        tree.node(tree.selected_id).thought.scenario if tree.selected_id else "abstain"
    )
    tot_p = (
        _probs(_best_by_scenario(final))
        if tree.selected_id
        else {s: 1 / 3 for s in SCENARIOS}
    )
    depth1 = [n for n in tree.nodes if n.depth == 1]
    lin = sorted(depth1, key=lambda n: (-n.score, n.thought.scenario))
    lin_choice = lin[0].thought.scenario if lin else "abstain"
    lin_p = _probs(_best_by_scenario(depth1))
    votes = defaultdict(float)
    for r in getattr(report, "specialists", ()) or ():
        votes[r.opinion.scenario] += r.opinion.confidence
    vote_choice = (
        max(SCENARIOS, key=lambda s: (votes.get(s, 0), -SCENARIOS.index(s)))
        if votes
        else "abstain"
    )
    return label, {
        "tot": (tot_choice, tot_p),
        "linear": (lin_choice, lin_p),
        "vote": (vote_choice, _probs(votes)),
    }


def evaluate(seeds=HELD_OUT) -> dict:
    """Accuracy counts an abstention as wrong; Brier is the 3-class sum of squares (0 best, 2 worst, 0.667 uniform)."""
    out = {
        m: {"correct": 0, "brier": 0.0, "brier_raw": 0.0, "abstain": 0}
        for m in ("tot", "linear", "vote")
    }
    n = 0
    for seed in seeds:
        label, methods = predict(seed)
        n += 1
        for m, (choice, p) in methods.items():
            out[m]["correct"] += choice == label
            out[m]["abstain"] += choice == "abstain"
            out[m]["brier_raw"] += _brier(p, label)
            out[m]["brier"] += _brier(calibrate(p, CALIBRATION_K[m]), label)
    return {
        "n": n,
        **{
            m: {
                "accuracy": v["correct"] / n,
                "brier": v["brier"] / n,
                "brier_raw": v["brier_raw"] / n,
                "abstention": v["abstain"] / n,
            }
            for m, v in out.items()
        },
    }
