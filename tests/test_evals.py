"""Runs the Phase 7 eval harness (offline, synthetic) and asserts the hard targets."""

import pytest

from evals import run as ev
from evals.golden import GOLDEN


@pytest.fixture(scope="module")
def result():
    return ev.compute()


def test_golden_set_shape():
    assert len(GOLDEN) == 30 and {t.kind for t in GOLDEN} == {
        "records",
        "market",
        "cross",
    }


def test_hard_targets_all_pass(result):
    failed = [
        (r["metric"], r["measured"])
        for r in result["rows"]
        if r["hard"] and not r["pass"]
    ]
    assert not failed, failed
    assert result["all_pass"]


def test_report_renders(result):
    md = ev.to_markdown(result)
    assert "| Metric |" in md and "Overall: PASS" in md


def test_integrations_and_userguide_cases_pass():
    from evals.integrations import run_integrations
    from evals.userguide import run_userguide

    assert all(p for _, p in run_integrations())
    assert all(p for _, p, _ in run_userguide())


def test_stability_smoke_two_runs_and_perturbations_restore_state():
    from evals import golden, injection, stability

    before_payloads, before_run = list(injection.PAYLOADS), golden.run_task
    r = stability.run(2)
    assert r["all_pass"] and r["runs"] == 2 and all(a["runs"] == 2 for a in r["rows"].values())
    for kind in stability.PERTURBATIONS:
        with stability.perturbed(kind, 0):
            pass
    assert injection.PAYLOADS == before_payloads and golden.run_task is before_run
def test_stress_set_shape_and_action_definition():
    from types import SimpleNamespace

    from evals.stress import STRESS, action_of
    from src.agents.orchestration.coordinator import Escalation

    kinds = {c.kind for c in STRESS}
    assert (
        len(STRESS) >= 40
        and {"ambiguous", "incomplete", "stale", "conflict", "clean"} <= kinds
    )
    assert all(
        sum(c.kind == k for c in STRESS) >= 8
        for k in ("ambiguous", "incomplete", "stale", "conflict", "clean")
    )
    rep = lambda *kinds: SimpleNamespace(
        escalations=[Escalation(k, "x") for k in kinds]
    )
    assert action_of(None, True) == "refuse"
    assert action_of(rep("clarify", "conflict"), False) == "ask"
    assert action_of(rep("unavailable", "stale"), False) == "escalate"
    assert (
        action_of(rep("unavailable", "single_source", "missing_statement"), False)
        == "caveat"
    )
    assert action_of(rep(), False) == "answer"


def test_every_golden_task_and_stress_case_labels_expected_agents():
    from evals.stress import STRESS

    assert all(t.expected_agents for t in GOLDEN)
    assert all(
        c.expected_agents or c.expected_action in ("ask", "refuse") for c in STRESS
    )


def test_escalation_metrics_count_labels_not_outputs():
    from types import SimpleNamespace

    from evals.stress import escalation_metrics

    r = lambda want, got: SimpleNamespace(
        case=SimpleNamespace(expected_action=want),
        action=got,
        report=SimpleNamespace(escalations=[1] * (got in ("ask", "escalate"))),
    )
    m = escalation_metrics(
        [
            r("escalate", "escalate"),
            r("ask", "answer"),
            r("answer", "escalate"),
            r("caveat", "caveat"),
            r("refuse", "refuse"),
        ]
    )
    assert (
        (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
        and m["precision"] == 0.5
        and m["recall"] == 0.5
    )


def test_review_entries_become_unlabelled_stubs_only():
    import pytest as _pytest

    from evals.review_cases import UNLABELLED, stub_cases
    from src.agents import store

    entries = [
        {
            "kind": "review",
            "run_id": "r1",
            "escalation_kind": "conflict",
            "claim_key": "bill:P:2026-09-30",
            "decision": d,
        }
        for d in ("accept", "dismiss", "correct", "dismiss")
    ] + [{"kind": "slot_run"}]
    stubs = stub_cases(entries)
    assert (
        len(stubs) == 1
        and stubs[0].expected_action == UNLABELLED
        and stubs[0].query == ""
    )
    with _pytest.raises(ValueError):
        store.log_review(None, "r", "conflict", "k", "approve")
