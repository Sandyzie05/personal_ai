"""P4: composed leakage across agents, merge and Core Memory (positive controls prove the scanner sees leaks)."""

from evals import disclosure
from evals.disclosure import ALLOWED_SINKS, SINKS, _merge_mislabelled, run_composed
from evals.golden import FakeVault, GOLDEN
from src.agents.orchestration import coordinator as coord
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.schemas import Finding, Plan, PlanStep


def test_composed_runs_have_no_canary_outside_allowed_sinks():
    r = run_composed()
    assert r.violations == [] and r.merge_mislabelled == 0
    assert r.runs >= 60 and r.hops_checked > 0 and r.sinks_scanned > r.runs


def test_allowed_sinks_table_is_closed_data():
    assert all(set(v) <= set(SINKS) for v in ALLOWED_SINKS.values())
    assert (
        ALLOWED_SINKS["records"] == frozenset()
        and ALLOWED_SINKS["brokerage"] == frozenset()
    )
    assert "quarantine_prompt" not in ALLOWED_SINKS["memory"] and not any(
        "bus" in v for v in ALLOWED_SINKS.values()
    )


def test_without_the_dependency_filter_the_eval_catches_upstream_findings_in_market_inputs(
    monkeypatch,
):
    monkeypatch.setattr(
        coord, "dependency_inputs", lambda src, dst, findings: tuple(findings)
    )
    r = run_composed()
    assert any("inputs:market received bill:" in v for v in r.violations)
    assert any(
        ":memory->inputs:market" in v for v in r.violations
    )  # Core Memory baseline would reach market


def test_a_compromised_worker_copying_a_record_canary_is_caught():
    t = next(t for t in GOLDEN if t.kind == "cross")
    leaked = Finding(
        agent="expense",
        claim_key="cashflow_delta:2026-Q3",
        value=1.0,
        summary=f"acct {t.canaries[0]}",
        confidence=0.5,
    )
    run = disclosure.run_task(
        t, wrap=lambda ws: {**ws, "expense": lambda s, i: [leaked]}
    )
    r = run_composed(extra_runs=[("leak", run, t.canaries)])
    assert any(v.startswith("leak:records->") for v in r.violations)


def test_merge_with_an_untrusted_contributor_is_untrusted_end_to_end():
    def worker(trust):
        return lambda step, inputs: [
            Finding(
                agent=step.agent,
                claim_key="k",
                value=1.0,
                summary="s",
                confidence=0.9,
                trust=trust,
            )
        ]

    plan = Plan(
        steps=(
            PlanStep(id="expense", agent="expense", goal="g"),
            PlanStep(id="email", agent="email", goal="g"),
        )
    )
    report = Coordinator(
        {"expense": worker("trusted"), "email": worker("untrusted")}
    ).run(plan)
    assert report.claims["k"].trust == "untrusted" and _merge_mislabelled(report) == 0
    report.claims["k"] = report.claims["k"].model_copy(update={"trust": "trusted"})
    assert _merge_mislabelled(report) == 1  # the check itself sees a mislabel


def test_untrusted_memory_is_recalled_labelled_and_never_reaches_quarantine_or_bus():
    t = next(t for t in GOLDEN if t.kind == "cross")
    vault, sinks = FakeVault(), disclosure._Sinks()
    disclosure._seed_memory(vault)
    report = disclosure._service_run(
        t, vault, sinks, t.query + " and summarize my emails memcanary", email=True
    )
    assert [m.trust for m in report.memory if m.key == "note:memcanary"] == [
        "untrusted"
    ]
    prompts = str(sinks.data["quarantine_prompt"])
    assert sinks.data["quarantine_prompt"] and not any(
        c in prompts for c in disclosure.MEMORY_CANARIES
    )
    assert not any(c in str(sinks.data["bus"]) for c in disclosure.MEMORY_CANARIES)
    assert (
        disclosure.MEMORY_CANARIES[1] in sinks.data["report"][0]
    )  # it is shown to the user, labelled


def test_stress_runs_feed_the_composed_scan_with_real_canaries():
    from evals.disclosure import run_composed, stress_runs

    extra = stress_runs()
    assert len(extra) >= 40 and sum(bool(c) for _, _, c, _ in extra) >= 30
    res = run_composed(extra)
    assert res.runs >= len(extra) and not res.violations
