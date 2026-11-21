"""Runs the Phase 7 eval harness (offline, synthetic) and asserts the hard targets."""

import pytest

from evals import run as ev
from evals.golden import GOLDEN


@pytest.fixture(scope="module")
def result():
    return ev.compute()


def test_golden_set_shape():
    assert len(GOLDEN) == 30 and {t.kind for t in GOLDEN} == {"records", "market", "cross"}


def test_hard_targets_all_pass(result):
    failed = [(r["metric"], r["measured"]) for r in result["rows"] if r["hard"] and not r["pass"]]
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


def test_stress_set_shape_and_action_definition():
    from types import SimpleNamespace

    from evals.stress import STRESS, action_of
    from src.agents.orchestration.coordinator import Escalation

    kinds = {c.kind for c in STRESS}
    assert len(STRESS) >= 40 and {"ambiguous", "incomplete", "stale", "conflict", "clean"} <= kinds
    assert all(sum(c.kind == k for c in STRESS) >= 8 for k in ("ambiguous", "incomplete", "stale", "conflict", "clean"))
    rep = lambda *kinds: SimpleNamespace(escalations=[Escalation(k, "x") for k in kinds])
    assert action_of(None, True) == "refuse"
    assert action_of(rep("clarify", "conflict"), False) == "ask"
    assert action_of(rep("unavailable", "stale"), False) == "escalate"
    assert action_of(rep("unavailable", "single_source", "missing_statement"), False) == "caveat"
    assert action_of(rep(), False) == "answer"


def test_every_golden_task_and_stress_case_labels_expected_agents():
    from evals.stress import STRESS

    assert all(t.expected_agents for t in GOLDEN)
    assert all(c.expected_agents or c.expected_action in ("ask", "refuse") for c in STRESS)
