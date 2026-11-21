import hashlib

import pytest

import tests.test_forecaster_outcomes as outcome_fixtures
from evals import tot_synth
from src.forecaster.outcomes import ForecastRecord, create_forecast
from src.forecaster.tot import search

THESIS = (
    "thesis_selected_id",
    "thesis_runner_up_id",
    "thesis_margin",
    "thesis_scenario",
)


def test_generator_is_deterministic_balanced_and_labels_are_planted():
    a, b = tot_synth.make_case(7), tot_synth.make_case(7)
    assert a[0].snapshot_id == b[0].snapshot_id and a[2] == b[2]
    labels = [tot_synth.make_case(s)[2] for s in tot_synth.HELD_OUT]
    assert {labels.count(x) for x in tot_synth.SCENARIOS} <= {
        33,
        34,
    }  # label = seed % 3, chosen before any evidence


def test_splits_are_disjoint_and_calibration_is_valid():
    dev, burned, held = map(set, (tot_synth.DEV, tot_synth.BURNED, tot_synth.HELD_OUT))
    assert not (dev & burned or dev & held or burned & held) and len(held) == 100
    p = tot_synth.calibrate({"bullish": 0.6, "base": 0.3, "bearish": 0.1}, 3)
    assert abs(sum(p.values()) - 1) < 1e-9 and p["bullish"] > 0.6


def test_held_out_gates_hold():
    r = tot_synth.evaluate(tot_synth.HELD_OUT)
    assert r["tot"]["accuracy"] >= max(r["linear"]["accuracy"], r["vote"]["accuracy"])
    assert r["tot"]["brier"] < min(r["linear"]["brier"], r["vote"]["brier"])


def _prediction_and_sessions(monkeypatch):
    seen = {}
    real = outcome_fixtures.create_forecast
    monkeypatch.setattr(
        outcome_fixtures,
        "create_forecast",
        lambda prediction, *a, **k: seen.setdefault("p", prediction)
        and real(prediction, *a, **k),
    )
    _, sessions = outcome_fixtures.fixture()
    return seen["p"], sessions


def _tree_for(prediction):
    """A real selected tree (synthetic case 1), re-bound to the fixture's snapshot id."""
    tree = search(tot_synth.make_case(1)[0])
    assert tree.selected_id
    return tree.model_copy(update={"snapshot_id": prediction.snapshot.snapshot_id})


def test_pre_p8_records_keep_their_forecast_id(monkeypatch):
    old, _ = outcome_fixtures.fixture()
    legacy = {
        k: v for k, v in old.to_api().items() if k not in THESIS
    }  # a record saved before P8
    reloaded = ForecastRecord.model_validate(legacy)
    pre_p8 = reloaded.model_dump_json(exclude=set(THESIS))
    assert (
        reloaded.forecast_id
        == old.forecast_id
        == hashlib.sha256(pre_p8.encode()).hexdigest()
    )


def test_forecast_persists_thesis_and_rejects_foreign_tree(monkeypatch):
    prediction, sessions = _prediction_and_sessions(monkeypatch)
    tree = _tree_for(prediction)
    rec = create_forecast(
        prediction, 5, sessions=sessions, tree=tree.model_dump(mode="json")
    )
    pick = tree.node(tree.selected_id)
    assert (
        rec.thesis_selected_id,
        rec.thesis_runner_up_id,
        rec.thesis_margin,
        rec.thesis_scenario,
    ) == (tree.selected_id, tree.runner_up_id, tree.margin, pick.thought.scenario)
    assert ForecastRecord.model_validate(rec.to_api()).forecast_id == rec.forecast_id
    with pytest.raises(ValueError):
        create_forecast(
            prediction, 5, sessions=sessions, tree=search(tot_synth.make_case(1)[0])
        )
