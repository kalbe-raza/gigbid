"""B2/B4 retrieval evaluation and ablation: every variant on every dev job with gold evidence, per persona.

    python -m evals.runners.rag_eval [--limit N] [--models]      -> evals/results/rag_variants.json (+ .csv)

Metrics against gold evidence items: Recall@5, MRR, nDCG@10 (graded by skill overlap). Breakdowns by persona, job
type and number of requirements (>= 4 is the "many requirements" slice the hypothesis is about). No LLM calls:
decomposed variants use the deterministic requirement extractor so the numbers are reproducible and free.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import time
from collections import defaultdict

from evals.runners.common import PERSONAS, RESULTS, build_indexes, dev, mean, sample, write_result
from rag.retriever import variant_config
from rag.variants.base import Pipeline, heuristic_requirements, ndcg

HYPOTHESIS = ("H-B (stated before running): requirement decomposition + hybrid BM25/dense retrieval raises evidence "
              "Recall@5 by at least 10 points over V0 on jobs with 4 or more requirements, and cross-encoder reranking "
              "adds precision (MRR) on top.")

VARIANTS = ["v0_naive", "v1_decompose", "v2_hybrid", "v3_rerank", "v4_outcome", "v8_corrective", "best"]
ABLATIONS = {  # shipped pipeline with one component removed (or the rejected components added back) at a time
    "best-minus-decomposition": {"query": "whole"},
    "best-minus-hybrid(dense only)": {"retrieval": "dense"},
    "best-minus-structured-chunks": {"chunking": "fixed"},
    "best-plus-rerank": {"rerank": "cross"},
    "best-plus-outcome": {"outcome": True},
}


def metrics(ranking: list[str], gold: list[dict]) -> dict:
    gids = [g["doc_id"] for g in gold]
    grades = {g["doc_id"]: g["grade"] for g in gold}
    top5 = ranking[:5]
    rec5 = len(set(top5) & set(gids)) / len(gids)
    rr = next((1 / (i + 1) for i, d in enumerate(ranking[:10]) if d in grades), 0.0)
    return {"recall@5": rec5, "mrr": rr, "ndcg@10": ndcg(ranking, grades, 10)}


def run_variant(name: str, cfg, rows_by_persona: dict) -> tuple[dict, list[dict]]:
    per_job, t0 = [], time.perf_counter()
    for persona, rows in rows_by_persona.items():
        pipe = Pipeline(cfg, persona)
        for r in rows:
            res = pipe.retrieve(r["job"], requirements=None, k=10)
            m = metrics(res.doc_ranking(), r["gold"]["relevant_evidence"])
            per_job.append({"variant": name, "persona": persona, "job_id": r["job"]["job_id"],
                            "kind": r["gold"]["kind"].split(":")[0],
                            "n_requirements": len(heuristic_requirements(r["job"])),
                            "latency_ms": res.trace.get("latency_ms"), **m})
    secs = time.perf_counter() - t0
    return summarise(per_job) | {"seconds": round(secs, 1)}, per_job


def summarise(rows: list[dict]) -> dict:
    def agg(rs):
        return {"n": len(rs), "recall@5": mean(r["recall@5"] for r in rs), "mrr": mean(r["mrr"] for r in rs),
                "ndcg@10": mean(r["ndcg@10"] for r in rs), "p95_latency_ms": sorted(r["latency_ms"] for r in rs)[
                    int(0.95 * (len(rs) - 1))] if rs else None}
    out = {"overall": agg(rows)}
    for key in ("persona", "kind"):
        groups = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        out[f"by_{key}"] = {k: agg(v) for k, v in sorted(groups.items())}
    out["by_requirements"] = {"4+": agg([r for r in rows if r["n_requirements"] >= 4]),
                              "<4": agg([r for r in rows if r["n_requirements"] < 4])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="jobs per persona (default: all with gold evidence)")
    ap.add_argument("--models", action="store_true", help="also compare embedding and reranker models (subset)")
    a = ap.parse_args()
    rows = {p: sample(dev(p), a.limit, key=lambda r: bool(r["gold"]["relevant_evidence"]) and not r["gold"]["scam"])
            for p in PERSONAS}
    build_indexes()
    results, all_rows = {}, []
    for v in VARIANTS:
        summ, per = run_variant(v, variant_config(v), rows)
        results[v] = summ
        all_rows += per
        print(f"{v:<30} R@5 {summ['overall']['recall@5']:.3f}  MRR {summ['overall']['mrr']:.3f}  "
              f"nDCG {summ['overall']['ndcg@10']:.3f}  ({summ['seconds']}s)", flush=True)
    best = variant_config("best")
    ablation = {}
    for name, change in ABLATIONS.items():
        if change.get("chunking") == "fixed":
            build_indexes(schemes=("fixed",))
        summ, per = run_variant(name, dataclasses.replace(best, **change), rows)
        ablation[name] = summ
        all_rows += per
        print(f"{name:<30} R@5 {summ['overall']['recall@5']:.3f}  MRR {summ['overall']['mrr']:.3f}  "
              f"nDCG {summ['overall']['ndcg@10']:.3f}", flush=True)
    models = {}
    if a.models:
        sub = {p: sample(rs, 40, seed=11) for p, rs in rows.items()}
        for label, change in {"embed=bge-small-en-v1.5 (shipped)": {},
                              "embed=nomic-embed-text-v1.5-Q": {"embed_model": "nomic-ai/nomic-embed-text-v1.5-Q"},
                              "+rerank MiniLM-L6": {"rerank": "cross"},
                              "+rerank bge-reranker-v2-m3-int8": {"rerank": "cross",
                                                                  "rerank_model": "BAAI/bge-reranker-v2-m3-int8"}}.items():
            cfg = dataclasses.replace(best, **change)
            if "embed_model" in change:
                build_indexes(models=[change["embed_model"]], schemes=("structured",))
            summ, _ = run_variant(label, cfg, sub)
            models[label] = summ
            print(f"{label:<40} R@5 {summ['overall']['recall@5']:.3f} MRR {summ['overall']['mrr']:.3f} "
                  f"p95 {summ['overall']['p95_latency_ms']}ms", flush=True)
    write_result("rag_variants", {"hypothesis": HYPOTHESIS, "n_jobs": {p: len(r) for p, r in rows.items()},
                                  "variants": results, "ablation_from_best": ablation, "model_comparison": models,
                                  "best_config": dataclasses.asdict(best)})
    with (RESULTS / "rag_per_job.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)


if __name__ == "__main__":
    main()
