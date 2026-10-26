"""Deterministic coordinator: validate plan, run independent steps in parallel, merge, escalate.

The LLM lives inside workers; delegation, merging, conflict detection and escalation are plain
code so they are testable and measurable (docs/platform_plan.md §3.2, §4).
"""

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .schemas import Finding, Plan, PlanStep

Worker = Callable[[PlanStep, Dict[str, Tuple[Finding, ...]]], Sequence[Finding]]
MAX_WAVES = 5  # bounded orchestration rounds (docs/forecasting_system.md)


_TLS = threading.local()


class Cancelled(Exception):
    """Raised by `check_cancel` inside a worker whose step timed out or whose run hit its deadline."""


def check_cancel() -> None:
    """Cooperative cancellation point for workers: call between provider calls / before expensive work.
    A hung call can't be interrupted, but a worker that checks stops as soon as control returns."""
    ev = getattr(_TLS, "event", None)
    if ev is not None and ev.is_set():
        raise Cancelled()


class PlanError(ValueError):
    """Plan references unknown agents/steps or has a dependency cycle."""


@dataclass
class StepResult:
    step_id: str
    agent: str
    status: str  # ok | failed | timeout | skipped
    started: float = 0.0
    ended: float = 0.0
    attempts: int = 0
    findings: Tuple[Finding, ...] = ()
    error: str = ""
    wave: int = 0


@dataclass(frozen=True)
class Conflict:
    claim_key: str
    options: Tuple[Tuple[str, str, float], ...]  # (agent, value, confidence)


@dataclass(frozen=True)
class Escalation:
    kind: str  # conflict | unavailable | stale | step_failed | untrusted_write | ungrounded
    detail: str


@dataclass
class RunReport:
    steps: List[StepResult]
    claims: Dict[str, Finding]
    conflicts: List[Conflict]
    escalations: List[Escalation]
    wall_s: float
    sum_step_s: float
    parallel_overlap: bool

    @property
    def needs_user(self) -> bool:
        return bool(self.escalations)


def validate_plan(plan: Plan, agents: Sequence[str]) -> None:
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate step ids")
    for s in plan.steps:
        if s.agent not in agents:
            raise PlanError(f"agent '{s.agent}' is not allowlisted")
        if any(d not in ids or d == s.id for d in s.depends_on):
            raise PlanError(f"step '{s.id}' has unknown/self dependency")
    done: set = set()
    for _ in range(len(ids)):
        ready = [
            s.id for s in plan.steps if s.id not in done and set(s.depends_on) <= done
        ]
        if not ready:
            break
        done.update(ready)
    if len(done) != len(ids):
        raise PlanError("dependency cycle")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "").lstrip("$"))
    except ValueError:
        return None


def _same(a, b) -> bool:
    x, y = _num(a), _num(b)
    if x is not None and y is not None:
        return abs(x - y) <= 0.01  # "1" == 1.0 ; cents tolerance
    return str(a).strip().lower() == str(b).strip().lower()


def merge(findings: Sequence[Finding]) -> Tuple[Dict[str, Finding], List[Conflict]]:
    """Group by claim_key. Disagreeing values are reported as conflicts, never averaged away."""
    groups: Dict[str, List[Finding]] = {}
    for f in findings:
        groups.setdefault(f.claim_key, []).append(f)
    claims: Dict[str, Finding] = {}
    conflicts: List[Conflict] = []
    for key, group in groups.items():
        valued = [f for f in group if f.value is not None and f.status != "unavailable"]
        distinct: List[Finding] = []
        for f in valued:
            if not any(_same(f.value, d.value) for d in distinct):
                distinct.append(f)
        best = max(group, key=lambda f: (f.status == "ok", f.confidence))
        trust = "untrusted" if any(f.trust == "untrusted" for f in group) else "trusted"
        evidence = tuple(dict.fromkeys(e for f in group for e in f.evidence_ids))
        if len(distinct) > 1:
            conflicts.append(
                Conflict(
                    key, tuple((f.agent, str(f.value), f.confidence) for f in distinct)
                )
            )
            claims[key] = best.model_copy(
                update={
                    "status": "unresolved",
                    "value": None,
                    "trust": trust,
                    "evidence_ids": evidence,
                    "confidence": min(f.confidence for f in distinct),
                }
            )
        else:
            claims[key] = best.model_copy(
                update={"trust": trust, "evidence_ids": evidence}
            )
    return claims, conflicts


def escalations_for(
    steps, claims, conflicts, now: datetime, max_age: timedelta
) -> List[Escalation]:
    out = [
        Escalation(
            "conflict",
            f"{c.claim_key}: " + " vs ".join(f"{a}={v}" for a, v, _ in c.options),
        )
        for c in conflicts
    ]
    out += [
        Escalation("step_failed", f"{s.step_id} ({s.agent}): {s.status}")
        for s in steps
        if s.status != "ok"
    ]
    for k, f in claims.items():
        # Verifier rule: a confident claim must cite evidence (grounding), else the user is told.
        if f.status == "ok" and f.value is not None and not f.evidence_ids:
            out.append(Escalation("ungrounded", f"{k}: no evidence cited"))
        if f.status in ("unavailable", "uncertain"):
            out.append(Escalation("unavailable", f"{k}: {f.status}"))
        elif f.as_of is not None and now - f.as_of > max_age:
            out.append(Escalation("stale", f"{k}: as of {f.as_of.date()}"))
    return out


def requires_confirmation(claims: Sequence[Finding]) -> bool:
    """Anything derived from untrusted content may not drive a write/memory action unattended."""
    return any(f.trust == "untrusted" for f in claims)


class Coordinator:
    def __init__(
        self,
        workers: Dict[str, Worker],
        *,
        max_workers: int = 4,
        max_retries: int = 1,
        deadline_s: float = 60.0,
        max_age: timedelta = timedelta(days=3),
        transient: Tuple[type, ...] = (TimeoutError, ConnectionError),
        recheck: Optional[Callable[[Conflict], Sequence[Finding]]] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.workers, self.max_workers, self.max_retries = (
            workers,
            max_workers,
            max_retries,
        )
        self.deadline_s, self.max_age, self.transient = deadline_s, max_age, transient
        self.recheck, self.clock = recheck, clock

    def _attempt(self, step: PlanStep, inputs, cancel: Optional[threading.Event] = None):
        """Runs in a pool thread and only RETURNS a result: a thread that outlives its timeout can
        therefore never mutate a report that was already built."""
        started, err = time.monotonic(), ""
        _TLS.event = cancel
        for attempt in range(1, self.max_retries + 2):
            try:
                check_cancel()
                findings = tuple(self.workers[step.agent](step, inputs))
                return "ok", findings, "", attempt, started, time.monotonic()
            except Cancelled:
                return "failed", (), "Cancelled", attempt, started, time.monotonic()
            except self.transient as exc:  # only classified-transient errors are retried
                err = type(exc).__name__
            except Exception as exc:
                return "failed", (), type(exc).__name__, attempt, started, time.monotonic()
        return "failed", (), err, self.max_retries + 1, started, time.monotonic()

    def _run_wave(self, ready, results, wave: int, t0: float) -> None:
        pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="agent")  # fresh per wave: hung
        try:  # threads from an earlier wave cannot starve this one
            futs, cancels = {}, []
            for s in ready:
                inputs = {d: results[d].findings for d in s.depends_on}
                ev = threading.Event()
                cancels.append(ev)
                futs[pool.submit(self._attempt, s, inputs, ev)] = (s, time.monotonic() + s.timeout_s, ev)
            pending = set(futs)
            while pending:
                now = time.monotonic()
                stop = min(min(futs[f][1] for f in pending), t0 + self.deadline_s)
                done, pending = wait(pending, timeout=max(0.0, stop - now), return_when=FIRST_COMPLETED)
                for f in done:
                    s = futs[f][0]
                    status, findings, err, attempts, started, ended = f.result()
                    results[s.id] = StepResult(s.id, s.agent, status, started, ended, attempts, findings, err, wave)
                now = time.monotonic()
                for f in list(pending):
                    if now >= futs[f][1] or now - t0 >= self.deadline_s:
                        s = futs[f][0]
                        results[s.id] = StepResult(s.id, s.agent, "timeout", futs[f][1] - s.timeout_s, now, 1,
                                                   (), "timeout", wave)
                        futs[f][2].set()  # tell the lingering worker to stop at its next check_cancel()
                        pending.discard(f)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def run(self, plan: Plan) -> RunReport:
        validate_plan(plan, tuple(self.workers))
        t0 = time.monotonic()
        results: Dict[str, StepResult] = {}
        for wave in range(1, MAX_WAVES + 1):
            pending = [s for s in plan.steps if s.id not in results]
            if not pending or time.monotonic() - t0 >= self.deadline_s:
                break
            ready = []
            for s in pending:
                deps = [results.get(d) for d in s.depends_on]
                if any(d is None for d in deps):
                    continue
                if any(d.status != "ok" for d in deps):
                    results[s.id] = StepResult(s.id, s.agent, "skipped", error="dependency failed", wave=wave)
                else:
                    ready.append(s)
            if ready:
                self._run_wave(ready, results, wave, t0)
        for s in plan.steps:
            results.setdefault(s.id, StepResult(s.id, s.agent, "skipped", error="round budget", wave=0))
        steps = [results[s.id] for s in plan.steps]
        findings = [f for r in steps for f in r.findings]
        claims, conflicts = merge(findings)
        if conflicts and self.recheck:  # one bounded re-check per conflict, then report as unresolved
            try:
                extra = [f for c in conflicts for f in self.recheck(c)]
                claims, conflicts = merge(findings + extra)
            except Exception:
                pass  # a failing re-check must not discard the run; the conflict stays reported
        timed = [r for r in steps if r.ended]
        overlap = any(
            a.wave == b.wave and a.started < b.ended and b.started < a.ended
            for i, a in enumerate(timed)
            for b in timed[i + 1 :]
        )
        return RunReport(
            steps,
            claims,
            conflicts,
            escalations_for(steps, claims, conflicts, self.clock(), self.max_age),
            time.monotonic() - t0,
            sum(r.ended - r.started for r in timed),
            overlap,
        )
