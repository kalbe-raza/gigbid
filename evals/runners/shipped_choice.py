"""Head-to-head of the shipped-config candidates on the full dev set (fast: no cross-encoder).
    python -m evals.runners.shipped_choice   -> evals/results/shipped_choice.json"""

from evals.runners.common import PERSONAS, build_indexes, dev, sample, write_result
from evals.runners.rag_eval import run_variant
from rag.variants.base import VariantConfig

CANDIDATES = {
    "decomposed+dense": VariantConfig("decomposed+dense", query="decomposed", retrieval="dense"),
    "decomposed+hybrid": VariantConfig("decomposed+hybrid", query="decomposed", retrieval="hybrid"),
    "decomposed+hybrid+outcome": VariantConfig("decomposed+hybrid+outcome", query="decomposed", retrieval="hybrid", outcome=True),
    "decomposed+dense+outcome": VariantConfig("decomposed+dense+outcome", query="decomposed", retrieval="dense", outcome=True),
}

if __name__ == "__main__":
    rows = {p: sample(dev(p), None, key=lambda r: bool(r["gold"]["relevant_evidence"]) and not r["gold"]["scam"]) for p in PERSONAS}
    build_indexes(schemes=("structured",))
    out = {}
    for name, cfg in CANDIDATES.items():
        out[name], _ = run_variant(name, cfg, rows)
        o = out[name]["overall"]
        print(f"{name:<28} R@5 {o['recall@5']:.3f} MRR {o['mrr']:.3f} nDCG {o['ndcg@10']:.3f} 4+req R@5 {out[name]['by_requirements']['4+']['recall@5']}", flush=True)
    write_result("shipped_choice", {"candidates": out})
