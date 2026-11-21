"""P1 labelled stress set: each case states its expected ACTION and claims in advance (W5, W6).

Cases run through the real product path (`service.run_query`: policy screen -> ambiguity check -> rule planner ->
Coordinator -> verifier) over synthetic records and fake providers. A case passes only if `action_of` matches the label
and every expected claim matches. Labels are written here, never derived from the system's output."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agents.orchestration.bus import MessageBus
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.policy import RequestRefused
from src.agents.orchestration.schemas import Plan
from src.agents.orchestration.service import run_query
from src.agents.orchestration.verifier import records_evidence
from src.agents.orchestration.workers import expense_worker, market_worker, plan_for

from .golden import (
    GOLDEN,
    EXPECTED_AGENTS,
    FakeProviders,
    FakeVault,
    _eq,
    forecaster_stub,
)

ACTIONS = ("answer", "caveat", "ask", "escalate", "refuse")
ESCALATE_KINDS = {
    "conflict",
    "step_failed",
    "ungrounded",
    "unsupported",
    "stale",
    "untrusted_write",
}
CAVEAT_KINDS = {"unavailable", "single_source", "missing_statement"}


@dataclass(frozen=True)
class StressCase:
    id: str
    kind: str  # ambiguous | incomplete | stale | conflict | clean | wrong_record
    query: str
    records: Tuple[Dict[str, Any], ...] = ()
    market: Dict[str, str] = field(default_factory=dict)
    expected_action: str = "answer"
    expected_claims: Dict[str, Any] = field(
        default_factory=dict
    )  # key -> value | ("status", s)
    expected_agents: frozenset = frozenset()
    age_days: int = 0
    provider_mode: str = "normal"
    overdue: Tuple[Dict[str, Any], ...] = ()  # ledger snapshot (P12)
    tamper: Optional[str] = (
        None  # wrong_period | wrong_provider | wrong_account (P2 seeded wrong-record citation)
    )


def action_of(report, refused: bool) -> str:
    """The ONLY definition of a run's action (P1)."""
    if refused:
        return "refuse"
    kinds = {e.kind for e in report.escalations}
    if "clarify" in kinds:
        return "ask"
    if kinds & ESCALATE_KINDS:
        return "escalate"
    if kinds and kinds <= CAVEAT_KINDS:
        return "caveat"
    return "answer"


def claims_ok(expected: Dict[str, Any], report) -> Tuple[bool, List[str]]:
    bad = []
    for key, want in expected.items():
        f = report.claims.get(key) if report is not None else None
        if f is None:
            bad.append(f"missing {key}")
        elif isinstance(want, tuple):
            if f.status != want[1]:
                bad.append(f"{key} status {f.status}")
        elif f.status != "ok" or not _eq(f.value, want):
            bad.append(f"{key}={f.value} ({f.status}) != {want}")
    return not bad, bad


# ---- seeded wrong-record citation (P2): the worker cites a real record that is NOT the claim's record ----


def _tamper(kind: str, records: List[Dict[str, Any]]) -> Callable:
    def wrap(inner):
        def run(step, inputs):
            out = list(inner(step, inputs))
            by_key = {r["storage_key"]: r for r in records}
            for i, f in enumerate(out):
                if (
                    not f.claim_key.startswith("bill:")
                    or not f.evidence_ids
                    or f.evidence_ids[0] not in by_key
                ):
                    continue
                mine = by_key[f.evidence_ids[0]]
                other = next(
                    (r for r in records if r is not mine and _differs(kind, mine, r)),
                    None,
                )
                if (
                    other is not None
                ):  # right figure (the other record's total), wrong record cited
                    out[i] = f.model_copy(
                        update={
                            "value": float(other["extraction"]["total"]),
                            "evidence_ids": (other["storage_key"],),
                        }
                    )
                    break
            return out

        return run

    return wrap


def _differs(kind, a, b) -> bool:
    ma, mb, ea, eb = (
        a.get("metadata") or {},
        b.get("metadata") or {},
        a["extraction"],
        b["extraction"],
    )
    if kind == "wrong_period":
        return ma.get("provider") == mb.get("provider") and ea.get(
            "period_end"
        ) != eb.get("period_end")
    if kind == "wrong_provider":
        return ma.get("provider") != mb.get("provider") and ea.get(
            "period_end"
        ) == eb.get("period_end")
    return ma.get("account_label") != mb.get("account_label") and ea.get(
        "period_end"
    ) == eb.get("period_end")


def build_coordinator(case: StressCase, *, b1: bool = False) -> Coordinator:
    vault = FakeVault()
    bus = MessageBus(vault)
    prov = FakeProviders(case.market, case.provider_mode, case.age_days)
    recs = list(case.records)
    expense = expense_worker(lambda: recs, lambda: list(case.overdue))
    if case.tamper:
        expense = _tamper(case.tamper, recs)(expense)
    workers = {
        "expense": expense,
        "market": market_worker(prov, bus),
        "forecaster": forecaster_stub(case.market, prov.requests),
    }
    c = Coordinator(workers)
    if not b1:
        c.evidence, c.records = records_evidence(recs), recs
    return c


@dataclass
class StressResult:
    case: StressCase
    action: str
    claims_pass: bool
    detail: List[str]
    report: Any = None

    @property
    def passed(self) -> bool:
        return self.action == self.case.expected_action and self.claims_pass


def run_case(case: StressCase) -> StressResult:
    coordinator = build_coordinator(case)
    try:
        report, refused = run_query(coordinator, case.query), False
    except RequestRefused:
        report, refused = None, True
    action = action_of(report, refused)
    ok, bad = (
        claims_ok(case.expected_claims, report)
        if report is not None
        else (not case.expected_claims, [])
    )
    return StressResult(case, action, ok, bad, report)


def run_case_b1(case: StressCase) -> StressResult:
    """B1: single agent, first routed domain only; no policy screen, no clarify, no verifier."""
    try:
        plan = plan_for(case.query)
    except ValueError:
        return StressResult(case, "answer", False, ["B1: nothing to delegate"])
    first = plan.steps[0].agent
    keep = {"expense"} if first == "expense" else {"forecaster", "market"}
    ids = {s.id for s in plan.steps if s.agent in keep}
    plan = Plan(
        steps=tuple(
            s.model_copy(
                update={"depends_on": tuple(d for d in s.depends_on if d in ids)}
            )
            for s in plan.steps
            if s.agent in keep
        )
    )
    report = build_coordinator(case, b1=True).run(plan)
    ok, bad = claims_ok(case.expected_claims, report)
    return StressResult(case, action_of(report, False), ok, bad, report)


# ---- the cases ----


def _strip_start(rec, start=None):
    ex = {k: v for k, v in rec["extraction"].items() if k != "period_start"}
    if start:
        ex["period_start"] = start
    return {**rec, "extraction": ex}


def _bills(task) -> Dict[str, Any]:
    return {k: v for k, v in task.expected.items() if k.startswith("bill:")}


def _build() -> List[StressCase]:
    cases: List[StressCase] = []
    records = [t for t in GOLDEN if t.kind == "records"]
    cross = [t for t in GOLDEN if t.kind == "cross"]
    market = [t for t in GOLDEN if t.kind == "market"]
    live = lambda t: "unavailable" not in t.market.values()

    # clean: well-formed questions with complete, fresh, agreeing data -> answer with every figure right
    for t in (
        records[:4]
        + [t for t in cross if live(t)][:3]
        + [t for t in market if live(t)][:3]
    ):
        cases.append(
            StressCase(
                f"clean-{t.id}",
                "clean",
                t.query,
                t.records,
                t.market,
                "answer",
                dict(t.expected),
                EXPECTED_AGENTS[t.kind],
            )
        )
    for i, q in enumerate(
        ("Buy 10 shares of NVDA for me.", "Pay my electricity bill now.")
    ):  # out of scope
        cases.append(
            StressCase(
                f"clean-refuse-{i}", "clean", q, records[0].records, {}, "refuse"
            )
        )

    # incomplete: one or two months of a quarter on file, or a market feed that reports unavailable -> caveat
    for t in records[:4]:
        recs = tuple(
            _strip_start(r)
            if r["extraction"]["period_end"].startswith("2026-09")
            else r
            for r in t.records
        )
        cases.append(
            StressCase(
                f"incomplete-1m-{t.id}",
                "incomplete",
                t.query,
                recs,
                {},
                "caveat",
                {**_bills(t), "cashflow_delta:2026-Q3": ("status", "uncertain")},
                EXPECTED_AGENTS["records"],
            )
        )
    for t in records[4:7]:
        recs = tuple(
            _strip_start(r, "2026-08-01")
            if r["extraction"]["period_end"].startswith("2026-09")
            else r
            for r in t.records
        )
        cases.append(
            StressCase(
                f"incomplete-2m-{t.id}",
                "incomplete",
                t.query,
                recs,
                {},
                "caveat",
                {**_bills(t), "cashflow_delta:2026-Q3": ("status", "uncertain")},
                EXPECTED_AGENTS["records"],
            )
        )
    for t in [t for t in market if live(t)][:2]:
        sym = next(iter(t.market))
        cases.append(
            StressCase(
                f"incomplete-feed-{t.id}",
                "incomplete",
                t.query,
                (),
                {sym: "unavailable"},
                "caveat",
                {f"lean:{sym}": ("status", "unavailable")},
                EXPECTED_AGENTS["market"],
            )
        )

    # stale: 30-day-old bars, or the provider raising (feed down after one retry) -> escalate
    for t in [t for t in market if live(t)][:4] + [t for t in cross if live(t)][:2]:
        sym = next(iter(t.market))
        cases.append(
            StressCase(
                f"stale-{t.id}",
                "stale",
                t.query,
                t.records,
                t.market,
                "escalate",
                {f"outlook:{sym}:5s": t.expected[f"outlook:{sym}:5s"], **_bills(t)},
                EXPECTED_AGENTS[t.kind],
                age_days=30,
            )
        )
    for t in [t for t in cross if live(t)][:2]:
        sym = next(iter(t.market))
        cases.append(
            StressCase(
                f"stale-down-{t.id}",
                "stale",
                t.query,
                t.records,
                t.market,
                "escalate",
                {f"outlook:{sym}:5s": t.expected[f"outlook:{sym}:5s"], **_bills(t)},
                EXPECTED_AGENTS["cross"],
                provider_mode="raise",
            )
        )

    # conflict: same provider + period, different totals -> escalate, the bill unresolved (never a pick)
    for t in records[:5] + cross[:3]:
        r0 = dict(t.records[-1])
        r0["storage_key"], r0["extraction"] = (
            "rec-dup",
            {**r0["extraction"], "total": r0["extraction"]["total"] + 55},
        )
        key = f"bill:{r0['metadata']['provider']}:{r0['extraction']['period_end']}"
        cases.append(
            StressCase(
                f"conflict-{t.id}",
                "conflict",
                t.query,
                t.records + (r0,),
                t.market,
                "escalate",
                {key: ("status", "unresolved")},
                EXPECTED_AGENTS[t.kind],
            )
        )

    # wrong_record (P2): right figure, wrong record cited (period, provider or account) -> verifier escalates
    two_providers = [
        t for t in records if len({r["metadata"]["provider"] for r in t.records}) > 1
    ]
    for t in records[:3]:
        cases.append(
            StressCase(
                f"wrong-period-{t.id}",
                "wrong_record",
                t.query,
                t.records,
                {},
                "escalate",
                {},
                EXPECTED_AGENTS["records"],
                tamper="wrong_period",
            )
        )
    for t in two_providers[:2]:
        cases.append(
            StressCase(
                f"wrong-provider-{t.id}",
                "wrong_record",
                t.query,
                t.records,
                {},
                "escalate",
                {},
                EXPECTED_AGENTS["records"],
                tamper="wrong_provider",
            )
        )
    for i, q in enumerate(
        (
            "What was my credit card bill ending 1111 for Q3 2026?",
            "Show my Card 1111 credit card spending for 2026.",
        )
    ):
        cases.append(
            StressCase(
                f"wrong-account-{i}",
                "wrong_record",
                q,
                _two_cards(),
                {},
                "escalate",
                {},
                EXPECTED_AGENTS["records"],
                tamper="wrong_account",
            )
        )

    # ambiguous: no period for a single-amount question, two accounts and none named, or market words but no ticker
    for t in records[:3]:
        cases.append(
            StressCase(
                f"ambiguous-period-{t.id}",
                "ambiguous",
                "How much was my bill?",
                t.records,
                {},
                "ask",
            )
        )
    cards = _two_cards()
    for i, q in enumerate(
        (
            "What was my credit card bill for Q3 2026?",
            "How much is my credit card statement total for September 2026?",
            "Show my credit card spending this quarter.",
        )
    ):
        cases.append(
            StressCase(f"ambiguous-account-{i}", "ambiguous", q, cards, {}, "ask")
        )
    for i, q in enumerate(
        (
            "What should I watch in the stock market today?",
            "Give me a forecast for my next trade idea.",
        )
    ):
        cases.append(StressCase(f"ambiguous-ticker-{i}", "ambiguous", q, (), {}, "ask"))
    return cases


def _two_cards() -> Tuple[Dict[str, Any], ...]:
    out = []
    for label, base in (("Card 1111", 410.0), ("Card 2222", 95.0)):
        for end, start, bump in (
            ("2026-06-30", "2026-04-01", 0),
            ("2026-09-30", "2026-07-01", 30),
        ):
            out.append(
                {
                    "storage_key": f"cc-{label[-4:]}-{end}",
                    "metadata": {
                        "provider": "Contoso Bank",
                        "category": "credit_card",
                        "account_label": label,
                    },
                    "extraction": {
                        "total": base + bump,
                        "period_end": end,
                        "period_start": start,
                    },
                }
            )
    return tuple(out)


STRESS: List[StressCase] = _build()
# Ambiguous cases were reported-not-gated until P3's clarify step landed; P3 is in, so nothing is pending.
PENDING_P3: set = set()


def evaluate() -> Dict[str, Any]:
    results = [run_case(c) for c in STRESS]
    b1 = [run_case_b1(c) for c in STRESS]
    gated = [r for r in results if r.case.id not in PENDING_P3]
    agents = [
        (c.expected_agents, frozenset(s.agent for s in plan_for(c.query).steps))
        for c in list(GOLDEN) + STRESS
        if c.expected_agents
    ]
    wrong = [r for r in results if r.case.kind == "wrong_record"]
    caught = sum(
        r.action == "escalate"
        and any(
            e.kind == "unsupported" and "cited record" in e.detail
            for e in r.report.escalations
        )
        for r in wrong
    )
    return {
        "relevance": (caught, len(wrong)),
        "n": len(results),
        "gated": len(gated),
        "passed": sum(r.passed for r in gated),
        "b1_passed": sum(r.passed for r in b1),
        "by_kind": _by_kind(results),
        "failures": [
            (r.case.id, r.action, r.case.expected_action, r.detail[:3])
            for r in gated
            if not r.passed
        ],
        "tool_selection": (sum(want == got for want, got in agents), len(agents)),
        "results": results,
    }


def _by_kind(results) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, List[int]] = {}
    for r in results:
        k = out.setdefault(r.case.kind, [0, 0])
        k[0] += r.passed
        k[1] += 1
    return {k: tuple(v) for k, v in out.items()}
