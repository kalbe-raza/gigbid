"""Ablation of the shipped retrieval pipeline, one component removed at a time (B, R&D question B).

The ablation runs inside rag_eval.py (same jobs, same indexes); this script prints it and its per-persona / per-job-type
breakdown from evals/results/rag_variants.json, re-running rag_eval first if the file is missing.
    python -m evals.runners.ablation
"""

from __future__ import annotations

import json
import subprocess
import sys

from evals.runners.common import RESULTS


def main() -> None:
    f = RESULTS / "rag_variants.json"
    if not f.exists():
        subprocess.run([sys.executable, "-m", "evals.runners.rag_eval"], check=True)
    d = json.loads(f.read_text())
    best = d["variants"]["best"]["overall"]
    print(f"{'pipeline':<34}{'R@5':>7}{'MRR':>7}{'nDCG':>7}   delta R@5 vs shipped")
    print(f"{'shipped (best)':<34}{best['recall@5']:>7.3f}{best['mrr']:>7.3f}{best['ndcg@10']:>7.3f}")
    for name, v in d["ablation_from_best"].items():
        o = v["overall"]
        print(f"{name:<34}{o['recall@5']:>7.3f}{o['mrr']:>7.3f}{o['ndcg@10']:>7.3f}   {o['recall@5'] - best['recall@5']:+.3f}")
    print("\nby persona (shipped):", json.dumps(d["variants"]["best"]["by_persona"], indent=1))


if __name__ == "__main__":
    main()
