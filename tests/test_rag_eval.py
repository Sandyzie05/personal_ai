"""`make eval-rag` gates run under `make test` too, so a retrieval/grounding regression fails the suite."""

from evals.rag import run


def test_rag_eval_gates_pass_and_beat_baseline():
    res = run.score_config(run.CONFIGS[-1], run.build())
    for k, floor in run.GATES.items():
        assert res[k] >= floor, (k, res[k])
    for k, ceil in run.GATES_MAX.items():
        assert res[k] <= ceil, (k, res[k])
    base = __import__("json").loads(run.BASELINE.read_text())
    assert res["recall@5"] > base["recall@5"] and res["context_precision"] > base["context_precision"]
    assert res["index_embed_calls"] < base["index_embed_calls"]


def test_golden_set_is_about_80_questions_with_traps():
    c = run.build()
    kinds = {q["kind"] for q in c["questions"]}
    assert len(c["questions"]) >= 80 and {"absent", "duplicate", "corrected", "account", "aggregation"} <= kinds
