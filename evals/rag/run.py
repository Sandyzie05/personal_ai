"""`make eval-rag`: retrieval/context/grounding eval on the synthetic bill corpus (offline, deterministic).

Runs several pipeline configs on the same ~80 golden questions: `legacy` (pre-R0 per-line chunks, bare metadata,
category/provider filter only) is the frozen baseline (evals/rag/baseline.json); `final` is the shipping
config and is the only one gated. Gates are set from the baseline run and must never be lowered to pass.
Embeddings are a hashed bag-of-words stand-in (evals/rag/embedder.py): deltas are meaningful, absolutes are not
the real model's."""

import atexit
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from src.ai_engine.chat_engine import ChatEngine
from src.ai_engine.chroma_store import ChromaStore
from src.ai_engine.embeddings import EmbeddingsGenerator
from src.ai_engine.query_analysis import (build_rag_filter, detect_category_from_query,
                                          detect_provider_from_query, parse_relative_date_range)
from src.ai_engine.rag_engine import RAGEngine

from .corpus import build
from .embedder import HashEmbedClient

RESULTS = Path(__file__).parent.parent / "results"
BASELINE = Path(__file__).parent / "baseline.json"
K = 5
_TMP = tempfile.mkdtemp(prefix="rag_eval_")
atexit.register(shutil.rmtree, _TMP, True)
TODAY = None  # relative phrases ("last month") are not used by the golden set

OFF = {"hybrid": False, "dedupe_versions": False, "min_coverage": 0}  # dense-only, no dedupe, no coverage gate
CONFIGS: List[Dict[str, Any]] = [
    dict(name="legacy", chunker="line", rich=False, period=False, provider_any=False, store={**OFF, "header": False}),
    dict(name="r0_packed", chunker="packed", rich=False, period=False, provider_any=False, store={**OFF, "header": False}),
    dict(name="r2_header_period", chunker="packed", rich=True, period=True, provider_any=True, store=OFF),
    dict(name="r4_hybrid", chunker="packed", rich=True, period=True, provider_any=True),
]
CONFIGS.append({**CONFIGS[-1], "name": "final"})  # R5 context assembly + R6 grounding are measured on this one
# Hard gates on the `final` config (set from the baseline run, see docs/rag_design.md §3.7). Placeholders until R6.
GATES: Dict[str, float] = {"recall@5": 0.95, "faithfulness": 0.98, "fabrication_caught": 0.98, "abstention": 0.95,
                           "context_has_answer": 0.95}
GATES_MAX: Dict[str, float] = {"false_abstain": 0.05, "stale_in_context": 0.0, "dup_in_context": 0.0}


def _line_chunker(text: str, _cap: int) -> List[str]:
    return [l.strip() for l in text.split("\n") if l.strip()]


class Pipeline:
    def __init__(self, cfg: Dict[str, Any], corpus: Dict[str, Any]):
        self.cfg, self.corpus = cfg, corpus
        self.tmp = _TMP  # chromadb allows one ephemeral system per process: share the dir, unique collections
        self.client = HashEmbedClient()
        gen = EmbeddingsGenerator(client=self.client)
        store = ChromaStore(embedding_generator=gen, persist_directory=self.tmp,
                            collection_name=f"eval_{cfg['name']}", chunker=_line_chunker if cfg["chunker"] == "line" else None,
                            **cfg.get("store", {}))
        self.rag = RAGEngine(embedding_generator=gen, chroma_store=store, **cfg.get("rag", {}))
        self.providers = sorted({d["metadata"]["provider"] for d in corpus["docs"].values()})
        texts, metas = [], []
        for key, data in corpus["docs"].items():
            texts.append(ChatEngine._data_to_text(None, data))
            m = ChatEngine._rag_metadata_for(None, key, data)
            if not cfg["rich"]:
                m = {k: m[k] for k in ("storage_key", "category", "provider") if k in m}
            metas.append(m)
        self.rag.add_documents(texts, metas)
        self.index_embed_calls = self.client.calls
        self.index_http_calls = self.client.http_calls

    def close(self):
        self.rag._get_chroma_store().reset()

    def where(self, q: str):
        cat = detect_category_from_query(q)
        prov = detect_provider_from_query(q, self.providers) if (cat or self.cfg["provider_any"]) else None
        rng = parse_relative_date_range(q, TODAY) if self.cfg["period"] else None
        return build_rag_filter(cat, prov, rng)

    def ask(self, q: str) -> Dict[str, Any]:
        t = time.perf_counter()
        ctx = self.rag.get_context_for_query(q, where=self.where(q), k=K)
        return {"context": ctx, "chunks": self.rag._get_chroma_store().last_retrieval(),
                "ms": (time.perf_counter() - t) * 1000}


def score_config(cfg: Dict[str, Any], corpus: Dict[str, Any]) -> Dict[str, Any]:
    p = Pipeline(cfg, corpus)
    try:
        ans = [q for q in corpus["questions"] if q["gold"]]
        absent = [q for q in corpus["questions"] if not q["gold"]]
        rec, mrr, prec, has, stale, dup, fabn, ms, chars, false_abs = [], [], [], [], [], [], [], [], [], 0
        for q in ans:
            r = p.ask(q["text"])
            ms.append(r["ms"]); chars.append(len(r["context"]))
            keys = [c["metadata"].get("storage_key") for c in r["chunks"]]
            gold_all = {k for g in q["gold"] for k in g}
            rec.append(sum(any(k in g for k in keys) for g in q["gold"]) / len(q["gold"]))
            first = next((i for i, k in enumerate(keys, 1) if k in gold_all), None)
            mrr.append(1 / first if first else 0.0)
            prec.append(sum(k in gold_all for k in keys) / len(keys) if keys else 0.0)
            has.append(all(a in r["context"] for a in q["answers"]))
            false_abs += not r["context"]
            if q["stale"]:
                stale.append(any(s in r["context"] for s in q["stale"]))
            if q["dup"]:
                dup.append(r["context"].count(q["answers"][0]) > 1)
            fabn.append((q, r))
        abst = sum(not p.ask(q["text"])["context"] for q in absent)
        out = {
            "recall@5": _m(rec), "mrr": _m(mrr), "context_precision": _m(prec), "context_has_answer": _m(has),
            "abstention": round(abst / len(absent), 4), "false_abstain": round(false_abs / len(ans), 4),
            "stale_in_context": _m(stale), "dup_in_context": _m(dup),
            "avg_retrieval_ms": round(sum(ms) / len(ms), 2), "avg_context_chars": round(sum(chars) / len(chars)),
            "index_embed_calls": p.index_embed_calls, "index_http_calls": p.index_http_calls, "n_answerable": len(ans), "n_absent": len(absent),
        }
        out.update(extra_metrics(p, fabn))
        return out
    finally:
        p.close()


def _mutate(figure: str, context: str):
    """Fabricate a plausible wrong figure (last digit changed); None if that happens to be in the context."""
    for i in range(len(figure) - 1, -1, -1):
        if figure[i].isdigit():
            for step in (3, 5, 7):
                wrong = figure[:i] + str((int(figure[i]) + step) % 10) + figure[i + 1:]
                if wrong not in context:
                    return wrong
    return None


def extra_metrics(p: Pipeline, asked) -> Dict[str, Any]:
    """faithfulness = share of faithful oracle answers (gold figures + the gold doc's [ev_id]) the grounding check
    accepts; fabrication_caught = share of answers with one figure altered that it rejects. Only for pipelines
    whose context carries evidence ids (R5+); earlier configs have no check at all."""
    from src.ai_engine.grounding import check
    from src.ai_engine.rag_engine import evidence_id

    ok = bad = caught = fab = 0
    for q, r in asked:
        ctx = r["context"]
        ids = [evidence_id(k) for g in q["gold"] for k in g if f"id={evidence_id(k)}" in ctx]
        if not ids or not all(a in ctx for a in q["answers"]):
            continue
        cite = "".join(f" [{i}]" for i in ids)  # a faithful answer cites every source it draws on
        ok += 1
        bad += not check("Answer: " + "; ".join(q["answers"]) + cite, ctx).ok
        wrong = _mutate(q["answers"][0], ctx)
        if wrong:
            fab += 1
            caught += not check("Answer: " + "; ".join([wrong] + q["answers"][1:]) + cite, ctx).ok
    if not ok:
        return {}
    return {"faithfulness": round(1 - bad / ok, 4), "fabrication_caught": round(caught / fab, 4) if fab else 1.0}


def _m(xs: List) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


ROWS = ["recall@5", "mrr", "context_precision", "context_has_answer", "abstention", "false_abstain", "stale_in_context",
        "dup_in_context", "faithfulness", "fabrication_caught", "avg_retrieval_ms", "avg_context_chars", "index_embed_calls", "index_http_calls"]


def main(argv: List[str]) -> int:
    corpus = build()
    if "--sweep" in argv:  # calibration: abstention vs false abstention across coverage thresholds
        print("| min_coverage | abstention | false_abstain | recall@5 |\n|---|---|---|---|")
        for t in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            r = score_config({**CONFIGS[-1], "name": f"sweep{t}", "store": {"min_coverage": t}}, corpus)
            print(f"| {t} | {r['abstention']} | {r['false_abstain']} | {r['recall@5']} |")
        return 0
    res = {c["name"]: score_config(c, corpus) for c in CONFIGS}

    if "--record-baseline" in argv:
        BASELINE.write_text(json.dumps(res["legacy"], indent=2) + "\n")
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else res["legacy"]
    names = [c["name"] for c in CONFIGS]
    lines = ["# RAG eval (synthetic corpus, hashed-BoW stand-in embedder, offline)", "",
             f"{len(corpus['docs'])} docs, {len(corpus['questions'])} questions. `baseline` = frozen pre-fix run "
             "(evals/rag/baseline.json). Absolutes are not the real model's; read deltas.", "",
             "| metric | baseline | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for row in ROWS:
        if any(row in r for r in res.values()):
            lines.append(f"| {row} | {base.get(row, '-')} | " + " | ".join(str(res[n].get(row, "-")) for n in names) + " |")
    fails = []
    final = res.get("final")
    for k, floor in GATES.items():
        if final is not None and final.get(k, 0) < floor:
            fails.append(f"{k}: {final.get(k)} < {floor}")
    for k, ceil in GATES_MAX.items():
        if final is not None and final.get(k, 1) > ceil:
            fails.append(f"{k}: {final.get(k)} > {ceil}")
    if GATES:
        lines += ["", "Gates on `final`: " + ", ".join(f"{k} >= {v}" for k, v in GATES.items()) + ", "
                  + ", ".join(f"{k} <= {v}" for k, v in GATES_MAX.items()),
                  "**Overall: " + ("FAIL (" + "; ".join(fails) + ")" if fails else "PASS") + "**"]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "rag_latest.md").write_text("\n".join(lines) + "\n")
    (RESULTS / "rag_latest.json").write_text(json.dumps(res, indent=2) + "\n")
    print("\n".join(lines))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
