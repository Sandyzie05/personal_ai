"""P7: repeat-run and changing-condition stability of the offline eval (`make eval-stability RUNS=10`).

Each run re-executes `evals.run.compute()` under one seeded perturbation (seed printed in the report). The gate is
strict: every hard row must pass in every run. A row below 100% is a bug or a finding, never noise."""

import argparse
import random
import re
import sys
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Dict, List

from . import golden, injection

PERTURBATIONS = (
    "baseline",
    "shuffle_records",
    "duplicate_reuploads",
    "paraphrase_injection",
    "latency_jitter",
    "max_workers_1",
    "max_workers_2",
    "inference_concurrency_1",
)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@contextmanager
def _patched_run_task(edit: Callable[[golden.Task, Dict[str, Any]], Dict[str, Any]]):
    """Swap run_task in every evals module that imported it (from-imports bind the name at import time)."""
    orig = golden.run_task

    def run_task(task, **kw):
        return orig(task, **edit(task, kw))

    mods = [
        m
        for name, m in list(sys.modules.items())
        if name.startswith("evals.") and getattr(m, "run_task", None) is orig
    ]
    for m in mods:
        m.run_task = run_task
    try:
        yield
    finally:
        for m in mods:
            m.run_task = orig


def _paraphrase(text: str, rng: random.Random) -> str:
    style = rng.choice(("upper", "spacing", "homoglyph", "title"))
    if style == "upper":
        return text.upper()
    if style == "spacing":
        return re.sub(r" ", lambda _: " " * rng.randint(1, 3), text)
    if style == "homoglyph":
        return injection.homoglyph(text)
    return text.title()


@contextmanager
def perturbed(kind: str, seed: int):
    rng = random.Random(seed)
    with ExitStack() as stack:
        if kind == "shuffle_records":

            def edit(task, kw):
                recs = list(
                    kw.get("records") if kw.get("records") is not None else task.records
                )
                rng.shuffle(recs)
                return {**kw, "records": recs}

            stack.enter_context(_patched_run_task(edit))
        elif kind == "duplicate_reuploads":

            def edit(task, kw):
                recs = list(
                    kw.get("records") if kw.get("records") is not None else task.records
                )
                dups = [
                    dict(r, storage_key=f"{r['storage_key']}-reupload")
                    for r in rng.sample(recs, min(2, len(recs)))
                ]
                return {**kw, "records": recs + dups}

            stack.enter_context(_patched_run_task(edit))
        elif kind == "latency_jitter":

            def edit(task, kw):
                inner = kw.get("wrap")

                def wrap(workers):
                    workers = inner(workers) if inner else workers
                    return {
                        k: golden.with_latency(w, rng.uniform(0, 0.2))
                        for k, w in workers.items()
                    }

                return {**kw, "wrap": wrap}

            stack.enter_context(_patched_run_task(edit))
        elif kind.startswith("max_workers_"):
            n = int(kind.rsplit("_", 1)[1])
            # setdefault: rows that compare parallel vs sequential (latency, B2) keep their explicit setting
            stack.enter_context(
                _patched_run_task(lambda task, kw: {"max_workers": n, **kw})
            )
        elif kind == "paraphrase_injection":
            orig = list(injection.PAYLOADS)
            injection.PAYLOADS[:] = [
                (c, loc, instr, _paraphrase(raw, rng)) for c, loc, instr, raw in orig
            ]
            stack.callback(lambda: injection.PAYLOADS.__setitem__(slice(None), orig))
        elif kind == "inference_concurrency_1":
            import src.config as config

            old = config.PERSONAL_AI_INFERENCE_CONCURRENCY
            config.PERSONAL_AI_INFERENCE_CONCURRENCY = 1
            stack.callback(
                lambda: setattr(config, "PERSONAL_AI_INFERENCE_CONCURRENCY", old)
            )
        yield


def run(runs: int) -> Dict[str, Any]:
    from .run import compute

    per_row: Dict[str, Dict[str, Any]] = {}
    plan = [(PERTURBATIONS[i % len(PERTURBATIONS)], i) for i in range(runs)]
    for kind, seed in plan:
        with perturbed(kind, seed):
            result = compute()
        for row in result["rows"]:
            agg = per_row.setdefault(
                row["metric"],
                {
                    "hard": row["hard"],
                    "passes": 0,
                    "runs": 0,
                    "values": [],
                    "worst": None,
                },
            )
            agg["runs"] += 1
            agg["passes"] += bool(row["pass"])
            nums = _NUM.findall(str(row["measured"]))
            if nums:
                agg["values"].append(float(nums[0]))
            if not row["pass"] and agg["worst"] is None:
                agg["worst"] = f"{kind} (seed {seed})"
    gate = all(a["passes"] == a["runs"] for a in per_row.values() if a["hard"])
    return {"runs": runs, "plan": plan, "rows": per_row, "all_pass": gate}


def to_markdown(r: Dict[str, Any]) -> str:
    out = [
        f"# Stability ({r['runs']} runs)",
        "",
        "Runs (perturbation, seed): " + ", ".join(f"{k} ({s})" for k, s in r["plan"]),
        "",
        "| Metric | Hard | Pass rate | First value min / max | Worst run |",
        "|---|---|---|---|---|",
    ]
    for metric, a in r["rows"].items():
        span = f"{min(a['values']):g} / {max(a['values']):g}" if a["values"] else "—"
        out.append(
            f"| {metric} | {'yes' if a['hard'] else 'info'} | {a['passes']}/{a['runs']} | {span} | "
            f"{a['worst'] or '—'} |"
        )
    out += [
        "",
        f"**Stability gate: {'PASS' if r['all_pass'] else 'FAIL'}** (every hard row must pass in every run)",
    ]
    return "\n".join(out)


def main(argv: List[str] = None) -> int:
    from .run import RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--live", action="store_true", help="repeat evals.live (needs oMLX up; user-run)")
    ns = parser.parse_args(argv)
    if ns.live:  # per worker model: set PERSONAL_AI_OMLX_WORKER_MODEL before each invocation
        from . import live

        codes = [live.main() for _ in range(ns.runs)]
        print(f"Live stability: {codes.count(0)}/{ns.runs} runs passed (exit codes {codes})")
        return 0 if all(c == 0 for c in codes) else 1
    r = run(ns.runs)
    md = to_markdown(r)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "stability_latest.md").write_text(md + "\n")
    print(md)
    return 0 if r["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
