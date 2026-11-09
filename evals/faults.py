"""Fault injection over golden tasks. Each case reports (a) bounded recovery and, where the fault
is an uncertainty fault, (b) whether the system recognised it instead of answering confidently."""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.schemas import Plan

from .golden import GOLDEN, Run, Task, run_task, score

DEADLINE_S = 60.0
HANG_TIMEOUT_S = 0.3
MAX_RETRIES = 1
STATES = {"ok", "failed", "timeout", "skipped"}


def _pick(kind: str, avail: bool = True) -> List[Task]:
    return [t for t in GOLDEN if t.kind == kind and (not t.market or ("unavailable" in t.market.values()) != avail)]


CROSS, MARKET, RECORDS = _pick("cross"), _pick("market"), _pick("records")


def flaky(agent: str, fails: Optional[int], exc=TimeoutError):
    """fails=None -> always raise."""
    def wrap(workers):
        inner, n = workers[agent], {"n": 0}

        def w(step, inputs):
            n["n"] += 1
            if fails is None or n["n"] <= fails:
                raise exc("injected")
            return inner(step, inputs)
        return {**workers, agent: w}
    return wrap


def hang(agent: str, gate: threading.Event):
    def wrap(workers):
        return {**workers, agent: lambda s, i: gate.wait(10) or []}
    return wrap


def short_timeouts(plan: Plan) -> Plan:
    return Plan(steps=tuple(s.model_copy(update={"timeout_s": HANG_TIMEOUT_S}) for s in plan.steps))


@dataclass
class CaseResult:
    id: str
    category: str
    bounded: bool
    recognized: Optional[bool]  # None = not an uncertainty fault
    detail: str = ""


def _steps(run: Run) -> Dict[str, object]:
    return {s.agent: s for s in run.report.steps}


def _bounded(run: Run, wall: float, limit: float = DEADLINE_S) -> bool:
    r = run.report
    return (wall < limit and all(s.status in STATES for s in r.steps)
            and all(s.attempts <= 1 + MAX_RETRIES for s in r.steps)
            and (all(s.status == "ok" for s in r.steps) or (r.needs_user and any(
                e.kind == "step_failed" for e in r.escalations))))


def _timed(task: Task, **kw):
    t0 = time.monotonic()
    run = run_task(task, **kw)
    return run, time.monotonic() - t0


def run_cases() -> List[CaseResult]:
    out: List[CaseResult] = []

    def add(cid, cat, run, wall, ok, recognized=None, detail="", limit=DEADLINE_S):
        out.append(CaseResult(cid, cat, _bounded(run, wall, limit) and ok, recognized, detail))

    for t in CROSS[:3]:  # transient fault that recovers after exactly one retry
        for ag in ("expense", "forecaster", "market"):
            run, w = _timed(t, wrap=flaky(ag, 1))
            st = _steps(run)[ag]
            add(f"{t.id}/{ag}", "transient_recovers", run, w,
                st.status == "ok" and st.attempts == 2 and score(t, run.report)[0])
    for t in CROSS[3:5] + MARKET[:1]:  # transient that never clears: <=1 retry, explicit failure, escalation
        for ag in ("expense", "forecaster", "market") if t.kind == "cross" else ("forecaster", "market"):
            run, w = _timed(t, wrap=flaky(ag, None))
            st = _steps(run)[ag]
            add(f"{t.id}/{ag}", "transient_persistent", run, w, st.status == "failed" and st.attempts == 2)
    for t in CROSS[:2]:  # permanent error: no retry; dependents skipped, never run on missing inputs
        for ag in ("expense", "forecaster", "market"):
            run, w = _timed(t, wrap=flaky(ag, None, ValueError))
            st = _steps(run)[ag]
            deps_ok = ag != "expense" or _steps(run)["market"].status == "skipped"
            add(f"{t.id}/{ag}", "permanent_error", run, w, st.status == "failed" and st.attempts == 1 and deps_ok)
    for t in CROSS[:2]:  # hang: bounded by per-step timeout, not by the worker
        for ag in ("expense", "forecaster", "market"):
            gate = threading.Event()
            run, w = _timed(t, wrap=hang(ag, gate), plan_edit=short_timeouts)
            gate.set()
            add(f"{t.id}/{ag}", "hang", run, w, _steps(run)[ag].status == "timeout", limit=3.0)
    for t in CROSS[:3] + MARKET[:2]:  # provider reports unavailable -> explicit status, no invented lean
        t2 = Task(t.id, t.kind, t.query, t.records, {s: "unavailable" for s in t.market}, {}, t.canaries)
        sym = next(iter(t2.market))
        run, w = _timed(t2)
        c = run.report.claims.get(f"lean:{sym}")
        rec = c is not None and c.status == "unavailable" and c.value is None and any(
            e.kind == "unavailable" and f"lean:{sym}" in e.detail for e in run.report.escalations)
        add(t.id, "provider_unavailable", run, w, True, rec)
    for t in CROSS[:2]:  # provider raises (connection error): retried once then failed explicitly
        run, w = _timed(t, provider_mode="raise")
        st = _steps(run)["market"]
        add(t.id, "provider_error", run, w, st.status == "failed" and st.attempts == 2)
    for t in CROSS[:3] + MARKET[:2]:  # stale feed (bars 30 days old) must be flagged, not served as fresh
        sym = next(iter(t.market))
        run, w = _timed(t, age_days=30)
        rec = any(e.kind == "stale" and f"lean:{sym}" in e.detail for e in run.report.escalations)
        add(t.id, "stale_data", run, w, True, rec)
    for t in CROSS[:2] + RECORDS[:2]:  # conflicting duplicate statement for same provider+period
        r0 = dict(t.records[-1])
        r0["storage_key"], r0["extraction"] = "rec-dup", {**r0["extraction"], "total": r0["extraction"]["total"] + 55}
        key = f"bill:{r0['metadata']['provider']}:{r0['extraction']['period_end']}"
        run, w = _timed(t, records=list(t.records) + [r0])
        cl, rep = run.report.claims, run.report
        derived = [c for k, c in cl.items() if k.startswith(("cashflow_delta", "budget_constraint"))]
        rec = (cl[key].status == "unresolved" and cl[key].value is None and bool(rep.conflicts)
               and any(e.kind == "conflict" for e in rep.escalations) and all(c.status != "ok" for c in derived))
        add(t.id, "conflicting_records", run, w, True, rec)
    for t in CROSS[:2] + RECORDS[:1]:  # re-uploaded identical statement must not double count
        run, w = _timed(t, records=list(t.records) + [{**t.records[-1], "storage_key": "rec-reupload"}])
        add(t.id, "duplicate_records", run, w, score(t, run.report)[0])
    for t in CROSS[:2] + RECORDS[:1]:  # only one quarter on file -> delta uncertain, no budget claim
        one = [r for r in t.records if r["extraction"]["period_end"].startswith("2026-09")]
        run, w = _timed(t, records=one)
        c = run.report.claims.get("cashflow_delta")
        rec = (c is not None and c.status == "uncertain" and c.value is None
               and not any(k.startswith("budget_constraint") for k in run.report.claims))
        add(t.id, "insufficient_history", run, w, True, rec)
    for t in CROSS[:2] + RECORDS[:1]:  # no records at all
        run, w = _timed(t, records=[])
        c = run.report.claims.get("records")
        add(t.id, "no_records", run, w, True, c is not None and c.status == "unavailable" and c.value is None
            and not any(k.startswith(("bill:", "cashflow_delta:")) for k in run.report.claims))
    for t in CROSS[:2] + RECORDS[:1]:  # B15: a quarter with one month of data must not read as a confident delta
        part = [{**r, "extraction": {k: v for k, v in r["extraction"].items() if k != "period_start"}} for r in t.records]
        run, w = _timed(t, records=part)
        c = run.report.claims[next(k for k in run.report.claims if k.startswith("cashflow_delta"))]
        add(t.id, "partial_quarter", run, w, True, c.status == "uncertain" and "partial" in c.summary)
    for t in CROSS[:2] + RECORDS[:1]:  # B15: superseded documents are excluded, not summed
        old = {**t.records[-1], "storage_key": "rec-old", "metadata": {**t.records[-1]["metadata"], "superseded": True},
               "extraction": {**t.records[-1]["extraction"], "total": 99999}}
        run, w = _timed(t, records=list(t.records) + [old])
        add(t.id, "superseded_records", run, w, score(t, run.report)[0])
    for t in CROSS[:2] + RECORDS[:1]:  # B15: two categories in one question -> flagged, never silently mixed
        tagged = [{**r, "metadata": {**r["metadata"], "category": "gas"}} for r in t.records]
        other = [{**r, "storage_key": f"{r['storage_key']}-m", "metadata": {"provider": "FabrikamEval", "category": "mobile"}}
                 for r in t.records]
        run, w = _timed(t, records=tagged + other, plan_edit=lambda p: Plan(
            steps=tuple(x.model_copy(update={"category": None}) for x in p.steps)))
        c = run.report.claims[next(k for k in run.report.claims if k.startswith("cashflow_delta"))]
        add(t.id, "mixed_categories", run, w, True, c.status == "uncertain" and "mixed categories" in c.summary)
    return out
