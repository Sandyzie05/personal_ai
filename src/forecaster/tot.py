"""Tree-of-Thought thesis search over one frozen EvidenceSnapshot (design: docs/reasoning_design.md).

Roles: generator = deterministic thesis seeding from evidence families; evaluator = rubric scoring
plus the specialist swarm as critic; controller = beam search with a diversity slot; state = the
immutable ThoughtTree returned to the caller. The LLM critic runs at most once per search and only
when depth 1 is not decisive. Scores are uncalibrated heuristics, not probabilities.
"""

import hashlib
from datetime import timedelta
from statistics import pstdev
from typing import Callable, Literal, Optional

from pydantic import Field

from .. import config
from .models import Contract, EvidenceSnapshot, Score

Scenario = Literal["bullish", "base", "bearish"]
Driver = Literal["trend", "volatility", "macro", "filing", "theme", "news", "balance"]
SCENARIO = {1: "bullish", 0: "base", -1: "bearish"}
CONSERVATIVE = {
    "base": 0,
    "bearish": 1,
    "bullish": 2,
}  # tie-break: least aggressive call wins
FAMILY = {"macro": "macro", "filing": "filing", "curated": "theme", "news": "news"}
SENTIMENT = {"positive": 1, "neutral": 0, "negative": -1}
FRESH = timedelta(days=30)


class Thought(Contract):
    scenario: Scenario
    driver: Driver
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=8)


class Node(Contract):
    node_id: str
    parent_id: str | None
    depth: int = Field(ge=1, le=2)
    thought: Thought
    score: Score
    components: dict[str, float]
    status: Literal["kept", "pruned", "selected"]
    reason: str | None = None


class ThoughtTree(Contract):
    version: Literal["tot-thesis-v1"] = "tot-thesis-v1"
    snapshot_id: str
    nodes: tuple[Node, ...]
    selected_id: str | None
    runner_up_id: str | None
    margin: float | None
    termination: Literal["converged", "depth_limit", "all_pruned", "no_evidence"]
    outcome: Literal["selected", "abstained"]
    critic_calls: int

    def node(self, node_id):
        return next((n for n in self.nodes if n.node_id == node_id), None)


def _drivers(snapshot: EvidenceSnapshot):
    """(driver, direction -1/0/1, strength 0..1, evidence_ids) per evidence family present."""
    out = []
    market = [c.evidence_id for c in snapshot.evidence if c.source_type == "market"]
    if snapshot.market and len(snapshot.market.bars) >= 5 and market:
        closes = [b.close for b in snapshot.market.bars]
        trend = max(-1.0, min(1.0, (closes[-1] - closes[0]) / closes[0] * 3))
        out.append(("trend", (trend > 0.1) - (trend < -0.1), abs(trend), market[:8]))
        vol = min(1.0, pstdev([(b - a) / a for a, b in zip(closes, closes[1:])]) * 16)
        if vol >= 0.5:  # fragile tape is a bearish reasoning path in its own right
            out.append(("volatility", -1, vol, market[:8]))
    for source_type, name in FAMILY.items():
        cards = [c for c in snapshot.evidence if c.source_type == source_type]
        if cards:
            net = sum(SENTIMENT[c.sentiment] * c.confidence for c in cards) / len(cards)
            out.append(
                (
                    name,
                    (net > 0.1) - (net < -0.1),
                    abs(net),
                    tuple(c.evidence_id for c in cards[:8]),
                )
            )
    return out


def _fresh(snapshot, ids) -> float:
    cards = {c.evidence_id: c for c in snapshot.evidence}
    ages = [
        snapshot.as_of
        - (cards[i].published_at or cards[i].observed_at or cards[i].retrieved_at)
        for i in ids
    ]
    return sum(a <= FRESH for a in ages) / len(ages)


def _heuristic(thought: Thought, drivers, snapshot) -> dict:
    directional = [(d, s) for _, d, s, _ in drivers if d]
    total = sum(s for _, s in directional)
    net = sum(d * s for d, s in directional)
    if (
        thought.scenario == "base"
    ):  # base wins when the directional evidence cancels out
        support = 1 - abs(net) / total if total else 1.0
        breadth = sum(1 for _, d, _, _ in drivers if not d) / len(drivers)
    else:
        sign = 1 if thought.scenario == "bullish" else -1
        support = (sign * net / total + 1) / 2 if total else 0.5
        breadth = sum(1 for _, d, _, _ in drivers if d == sign) / len(drivers)
    return {
        "support": support,
        "breadth": breadth,
        "freshness": _fresh(snapshot, thought.evidence_ids),
    }


def _critic(thought: Thought, opinions) -> tuple[float, int]:
    """(critic score 0..1, confident contradictions). Critics citing the thesis's evidence weigh double."""
    weight = agree = vetoes = 0.0
    for o in opinions:
        a = (
            1
            if o.scenario == thought.scenario
            else -1
            if {o.scenario, thought.scenario} == {"bullish", "bearish"}
            else 0
        )
        w = o.confidence * (
            1 if set(o.evidence_ids) & set(thought.evidence_ids) else 0.5
        )
        weight, agree = weight + w, agree + w * a
        vetoes += a == -1 and o.confidence >= config.TOT_CRITIC_VETO_CONFIDENCE
    return ((agree / weight + 1) / 2 if weight else 0.5), int(vetoes)


def _id(parent, thought) -> str:
    return hashlib.sha256(f"{parent}|{thought.model_dump_json()}".encode()).hexdigest()[
        :16
    ]


def _rank(n: Node):
    return (-n.score, CONSERVATIVE[n.thought.scenario])


def _beam(nodes):
    """Best kept branch per scenario first, then fill by score. If only one scenario survives, one slot
    still holds the best other-scenario branch: a weak heuristic must not silently end a scenario."""
    ordered = sorted(nodes, key=_rank)
    kept = [n for n in ordered if n.status == "kept"]
    leaders = [n for i, n in enumerate(kept) if n.thought.scenario not in {m.thought.scenario for m in kept[:i]}]
    beam = (leaders + [n for n in kept if n not in leaders])[: config.TOT_BEAM_WIDTH]
    if beam and all(n.thought.scenario == beam[0].thought.scenario for n in beam):
        other = next((n for n in ordered if n.thought.scenario != beam[0].thought.scenario), None)
        if other is not None:
            other = other.model_copy(update={"status": "kept", "reason": "diversity slot"})
            beam = beam[: config.TOT_BEAM_WIDTH - 1] + [other]
    return beam


def _select(final):
    """Best score; ties (within TOT_TIE) go to broader evidence, then the more conservative scenario."""
    best = max(n.score for n in final)
    tied = [n for n in final if best - n.score <= config.TOT_TIE]
    return min(
        tied,
        key=lambda n: (
            -n.components.get("breadth", 0),
            CONSERVATIVE[n.thought.scenario],
            -n.score,
        ),
    )


def search(
    snapshot: EvidenceSnapshot, critique: Optional[Callable[[], object]] = None
) -> ThoughtTree:
    """`critique()` returns a ScenarioReport (or None); it is called at most once, only for depth 2."""
    drivers = _drivers(snapshot)
    if not drivers:
        return ThoughtTree(
            snapshot_id=snapshot.snapshot_id,
            nodes=(),
            selected_id=None,
            runner_up_id=None,
            margin=None,
            termination="no_evidence",
            outcome="abstained",
            critic_calls=0,
        )

    # Depth 1 - generate: one thesis per directional family, plus the "signals cancel" base thesis.
    thoughts = [
        Thought(scenario=SCENARIO[d], driver=name, evidence_ids=ids)
        for name, d, _, ids in sorted(drivers, key=lambda x: -x[2])
        if d
    ]
    base_ids = tuple(dict.fromkeys(i for _, _, _, ids in drivers for i in ids[:2]))[:8]
    thoughts = thoughts[: config.TOT_MAX_BRANCHES - 1] + [
        Thought(scenario="base", driver="balance", evidence_ids=base_ids)
    ]
    level = []
    for t in thoughts:  # evaluate: deterministic rubric
        c = _heuristic(t, drivers, snapshot)
        score = 0.55 * c["support"] + 0.25 * c["breadth"] + 0.20 * c["freshness"]
        weak = score < config.TOT_PRUNE_MIN
        level.append(
            Node(
                node_id=_id(snapshot.snapshot_id, t),
                parent_id=None,
                depth=1,
                thought=t,
                score=score,
                components=c,
                status="pruned" if weak else "kept",
                reason="below prune threshold" if weak else None,
            )
        )
    beam = _beam(level)
    in_beam = {n.node_id for n in beam}
    nodes = {
        n.node_id: n
        if n.node_id in in_beam or n.status == "pruned"
        else n.model_copy(update={"status": "pruned", "reason": "outside beam"})
        for n in level
    }
    nodes |= {n.node_id: n for n in beam}

    def finish(final, termination, calls):
        live = [n for n in final if n.status != "pruned"]
        if not live:
            return ThoughtTree(
                snapshot_id=snapshot.snapshot_id,
                nodes=tuple(nodes.values()),
                selected_id=None,
                runner_up_id=None,
                margin=None,
                termination="all_pruned",
                outcome="abstained",
                critic_calls=calls,
            )
        chosen = _select(live)
        others = sorted(
            (n for n in live if n.thought.scenario != chosen.thought.scenario),
            key=_rank,
        )
        runner = others[0] if others else None
        margin = chosen.score - runner.score if runner else chosen.score
        ok = chosen.score >= config.TOT_SELECT_MIN
        if ok:
            nodes[chosen.node_id] = chosen.model_copy(update={"status": "selected"})
        return ThoughtTree(
            snapshot_id=snapshot.snapshot_id,
            nodes=tuple(nodes.values()),
            selected_id=chosen.node_id if ok else None,
            runner_up_id=runner.node_id if runner else None,
            margin=margin,
            termination=termination,
            outcome="selected" if ok else "abstained",
            critic_calls=calls,
        )

    top = sorted(beam, key=_rank)
    rival = (
        next(
            (n.score for n in top if n.thought.scenario != top[0].thought.scenario), 0.0
        )
        if top
        else 0.0
    )
    converged = bool(top) and (
        top[0].score >= config.TOT_CONVERGE_SCORE
        and top[0].score - rival >= config.TOT_CONVERGE_MARGIN
    )
    if not top or converged or config.TOT_MAX_DEPTH < 2 or critique is None:
        return finish(beam, "converged" if converged else "depth_limit", 0)

    # Depth 2 - critique the survivors with the specialist swarm (one shared LLM review, not one per branch).
    report = critique()
    opinions = [
        r.opinion for r in getattr(report, "specialists", ()) if r.opinion is not None
    ]
    children = []
    for parent in beam:
        if not opinions:
            child = parent.model_copy(
                update={
                    "node_id": _id(parent.node_id, parent.thought),
                    "parent_id": parent.node_id,
                    "depth": 2,
                    "reason": "critic unavailable",
                }
            )
        else:
            critic, vetoes = _critic(parent.thought, opinions)
            veto = vetoes >= 2
            child = Node(
                node_id=_id(parent.node_id, parent.thought),
                parent_id=parent.node_id,
                depth=2,
                thought=parent.thought,
                score=0.6 * parent.score + 0.4 * critic,
                components=parent.components | {"critic": critic, "vetoes": vetoes},
                status="pruned" if veto else "kept",
                reason="critics contradict" if veto else None,
            )
        children.append(child)
        nodes[child.node_id] = child
    return finish(children, "depth_limit", 1)
