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


def test_stability_smoke_two_runs_and_perturbations_restore_state():
    from evals import golden, injection, stability

    before_payloads, before_run = list(injection.PAYLOADS), golden.run_task
    r = stability.run(2)
    assert r["all_pass"] and r["runs"] == 2 and all(a["runs"] == 2 for a in r["rows"].values())
    for kind in stability.PERTURBATIONS:
        with stability.perturbed(kind, 0):
            pass
    assert injection.PAYLOADS == before_payloads and golden.run_task is before_run
