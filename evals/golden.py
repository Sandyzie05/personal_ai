"""30-task synthetic golden set + fake providers. NO real data: every name/number is generated."""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agents.orchestration.bus import MessageBus
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.schemas import Finding, Plan
from src.agents.orchestration.workers import expense_worker, market_worker, plan_for
from src.forecaster.models import (
    EvidenceCard,
    MarketData,
    PriceBar,
    ProviderIssue,
    ProviderResult,
)

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
SYMBOLS = [
    "NVDA",
    "AMD",
    "MSFT",
    "AAPL",
    "TSLA",
    "INTC",
    "AVGO",
    "ORCL",
    "CSCO",
    "QCOM",
]
UTILS = ["electricity", "gas", "water", "internet", "utility"]


class FakeVault:
    def __init__(self):
        self.d: Dict[str, Any] = {}

    def store_data(self, k, v, **kw):
        self.d[k] = json.loads(json.dumps(v))

    def retrieve_data(self, k):
        return self.d.get(k)


@dataclass(frozen=True)
class Task:
    id: str
    kind: str  # records | market | cross
    query: str
    records: Tuple[Dict[str, Any], ...] = ()
    market: Dict[str, str] = field(
        default_factory=dict
    )  # symbol -> up | down | unavailable
    expected: Dict[str, Any] = field(
        default_factory=dict
    )  # claim_key -> value | ("status", s)
    canaries: Tuple[str, ...] = ()


def _rec(i, n, provider, total, end, canaries):
    return {
        "storage_key": f"rec-{i}-{n}",
        "metadata": {"provider": provider},
        "extraction": {"total": total, "period_end": end,
                       "period_start": {"06-30": "2026-04-01", "09-30": "2026-07-01"}[end[5:]]},  # quarterly statements
        # PII that must never leave the expense worker:
        "account_number": canaries[0],
        "ssn": canaries[1],
        "customer_email": canaries[2],
        "raw_text": f"Dear {canaries[3]}, acct {canaries[0]}",
    }


def _build() -> List[Task]:
    tasks: List[Task] = []
    for i in range(30):
        kind = ("records", "market", "cross")[i % 3]
        n = i // 3
        can = (
            f"{4000000000000 + i * 7919:013d}",
            f"900-{10 + i:02d}-{1000 + i}",
            f"canary{i}@pii-eval.test",
            f"ZQXCANARY{i}NAME",
        )
        sym, syms = SYMBOLS[n % len(SYMBOLS)], None
        direction = (
            ("up", "down", "unavailable")[(n + (i % 2)) % 3]
            if kind != "records"
            else None
        )
        records, expected = (), {}
        if kind != "market":
            p1 = f"Provider{i}A"
            a, b = 200 + 13 * i, 260 + 17 * i + (-90 if n % 2 else 0)
            rows = [(p1, a, "2026-06-30"), (p1, b, "2026-09-30")]
            tot_a, tot_b = a, b
            if n % 3 == 0:  # second utility, same quarters
                p2, c, d = f"Provider{i}B", 80 + i, 95 + 2 * i
                rows += [(p2, c, "2026-06-30"), (p2, d, "2026-09-30")]
                tot_a, tot_b = a + c, b + d
            records = tuple(
                _rec(i, k, p, t, e, can) for k, (p, t, e) in enumerate(rows)
            )
            for p, t, e in rows:
                expected[f"bill:{p}:{e}"] = float(t)
            delta = round((tot_b - tot_a) / 3, 2)
            expected["cashflow_delta:2026-Q3"] = delta
        if kind == "records":
            q = f"Show my {UTILS[n % len(UTILS)]} bills and spending."
        elif kind == "market":
            q = f"What should I watch on ${sym} today?"
        else:
            q = f"How do my {UTILS[n % len(UTILS)]} bills affect my budget and what should I watch on {sym}?"
            expected["budget_constraint:2026-Q3"] = (
                "up" if delta > 0 else "down" if delta < 0 else "flat"
            )
        if kind != "records":
            expected[f"outlook:{sym}:5s"] = (
                "constructive" if direction != "down" else "cautious"
            )
            expected[f"lean:{sym}"] = (
                ("status", "unavailable")
                if direction == "unavailable"
                else "bullish"
                if direction == "up"
                else "neutral"  # low-vol decline: RSI<30 scored oversold (recommend.technical_score)
            )
        tasks.append(
            Task(
                f"t{i:02d}-{kind}",
                kind,
                q,
                records,
                {sym: direction} if kind != "records" else {},
                expected,
                can,
            )
        )
    return tasks


GOLDEN: List[Task] = _build()


# ---- fake providers (deterministic, offline) ----
def market_data(symbol: str, direction: str, age_days: int = 0) -> MarketData:
    start = date.today() - timedelta(days=249 + age_days)
    closes = [100 * (1.004 if direction == "up" else 0.996) ** k for k in range(250)]
    bars = tuple(
        PriceBar(
            symbol=symbol,
            session=start + timedelta(days=k),
            close=c,
            source_id="yahoo-chart",
            retrieved_at=NOW,
        )
        for k, c in enumerate(closes)
    )
    card = EvidenceCard(
        source_id="yahoo-chart",
        source_type="market",
        symbol=symbol,
        title="Prices",
        summary=f"{direction} ramp",
        retrieved_at=NOW,
        confidence=0.8,
    )
    return MarketData(
        symbol=symbol,
        exchange="XNAS",
        currency="USD",
        bars=bars,
        price_convention="unadjusted_close",
        evidence=(card,),
    )


class FakeProviders:
    """Records every request it receives: that list is the egress-bound payload set."""

    def __init__(self, scenario: Dict[str, str], mode: str = "normal", age_days: int = 0):
        self.scenario, self.mode, self.age_days = scenario, mode, age_days
        self.requests: List[str] = []

    async def market(self, req):
        self.requests.append(req.model_dump_json())
        if self.mode == "raise":
            raise ConnectionError("provider down")
        d = self.scenario.get(req.symbol, "unavailable")
        if d == "unavailable":
            return ProviderResult(
                status="unavailable",
                issues=(
                    ProviderIssue(
                        source_id="yahoo-chart", code="unavailable", message="down"
                    ),
                ),
            )
        return ProviderResult(status="completed", value=market_data(req.symbol, d, self.age_days))


def evidence_id_for(symbol: str) -> str:
    return "ev:" + hashlib.sha256(f"fc-{symbol}".encode()).hexdigest()[:16]


def forecaster_stub(scenario: Dict[str, str], requests: Optional[List[str]] = None):
    def run(step, inputs):
        if requests is not None:
            requests.append(json.dumps({"symbol": step.symbol}))
        d = scenario.get(step.symbol, "up")
        return [
            Finding(
                agent="forecaster",
                claim_key=f"outlook:{step.symbol}:5s",
                value="cautious" if d == "down" else "constructive",
                summary="synthetic specialist merge",
                evidence_ids=(evidence_id_for(step.symbol),),
                confidence=0.7,
                as_of=datetime.now(timezone.utc),
            )
        ]

    return run


def with_latency(worker, seconds: float):
    def run(step, inputs):
        time.sleep(seconds)
        return worker(step, inputs)

    return run


@dataclass
class Run:
    report: Any
    bus: MessageBus
    providers: FakeProviders
    vault: FakeVault
    requests: List[str]
    plan: Plan


def run_task(
    task: Task,
    *,
    max_workers: int = 4,
    latency: float = 0.0,
    canaries: bool = True,
    wrap: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    plan_edit: Optional[Callable[[Plan], Plan]] = None,
    provider_mode: str = "normal", age_days: int = 0,
    coordinator_kw: Optional[Dict[str, Any]] = None,
    records: Optional[list] = None,
) -> Run:
    vault = FakeVault()
    bus = MessageBus(vault, canaries=task.canaries if canaries else ())
    prov = FakeProviders(task.market, provider_mode, age_days)
    recs = list(task.records if records is None else records)
    workers = {
        "expense": expense_worker(lambda: recs),
        "market": market_worker(prov, bus),
        "forecaster": forecaster_stub(task.market, prov.requests),
    }
    if latency:
        workers = {k: with_latency(w, latency) for k, w in workers.items()}
    if wrap:
        workers = wrap(workers)
    plan = plan_for(task.query)
    if plan_edit:
        plan = plan_edit(plan)
    rep = Coordinator(workers, max_workers=max_workers, **(coordinator_kw or {})).run(
        plan
    )
    return Run(rep, bus, prov, vault, prov.requests, plan)


def run_b1(task: Task) -> Run:
    """B1: single-domain pipeline, no coordinator logic across domains. Answers only the first
    domain it routes to (bills OR market), so cross-domain questions are structurally incomplete."""
    first = plan_for(task.query).steps[0].agent
    keep = {"expense"} if first == "expense" else {"forecaster", "market"}

    def edit(p: Plan) -> Plan:
        steps = tuple(
            s.model_copy(
                update={
                    "depends_on": tuple(
                        d
                        for d in s.depends_on
                        if d in {x.id for x in p.steps if x.agent in keep}
                    )
                }
            )
            for s in p.steps
            if s.agent in keep
        )
        return Plan(steps=steps)

    return run_task(task, plan_edit=edit)


def _eq(got, want) -> bool:
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(got - want) <= 0.01
    return got == want


def score(task: Task, report) -> Tuple[bool, List[str]]:
    """Task succeeds only if every expected claim is present with the expected value/status."""
    bad = []
    for key, want in task.expected.items():
        f = report.claims.get(key)
        if f is None:
            bad.append(f"missing {key}")
        elif isinstance(want, tuple):
            if f.status != want[1]:
                bad.append(f"{key} status {f.status}")
        elif f.status != "ok" or not _eq(f.value, want):
            bad.append(f"{key}={f.value} ({f.status}) != {want}")
    return not bad, bad
