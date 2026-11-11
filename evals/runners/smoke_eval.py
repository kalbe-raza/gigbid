"""CI smoke eval (E2): 30 dev jobs, deterministic, no paid calls. Fails (exit 1) when a gate in evals/thresholds.yaml
is crossed:
  * evidence Recall@5 of the shipped retrieval config
  * bid-or-skip F1 (risk LLM off: patterns + client history; Prompt Guard only if a key is present)
  * unsupported claims per proposal, re-verified deterministically on the drafts stored by the last outcome run

    python -m evals.runners.smoke_eval --jobs-per-persona 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import yaml

from agent.settings import ROOT
from evals.runners import decision_eval
from evals.runners.common import (
    PERSONAS,
    RESULTS,
    LocalToolBox,
    build_indexes,
    dev,
    mean,
    sample,
    write_result,
)
from evals.runners.rag_eval import metrics
from rag.retriever import variant_config
from rag.variants.base import Pipeline
from rag.verify import CITE, verify_text


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-per-persona", type=int, default=10)
    a = ap.parse_args()
    th = yaml.safe_load((ROOT / "evals/thresholds.yaml").read_text())
    build_indexes(schemes=("structured", "fixed"))
    cfg = variant_config("best")
    rec, dec_rows = [], []
    clients = json.loads((ROOT / "gigsim/seed/clients.json").read_text())
    stats = json.loads((ROOT / "data/market/market_stats.json").read_text())
    for p in PERSONAS:
        rows = sample(dev(p), a.jobs_per_persona, seed=101)
        pipe = Pipeline(cfg, p)
        for r in rows:
            if r["gold"]["relevant_evidence"] and not r["gold"]["scam"]:
                rec.append(metrics(pipe.retrieve(r["job"], k=10).doc_ranking(), r["gold"]["relevant_evidence"])["recall@5"])
        profile = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text()) | {"domain": p}
        seen: list[dict] = []
        for r in rows:
            dec_rows.append(await decision_eval.run_job(r, p, profile, clients, stats, "never", seen))
            seen.append({"job_id": r["job"]["job_id"], "title": r["job"]["title"], "description": r["job"]["description"]})
    dec = decision_eval.summarise(dec_rows)
    unsup = []
    for f in sorted(RESULTS.glob("gigsim_outcomes_*_per_job.jsonl"))[:2]:
        for line in f.read_text().splitlines()[:10]:
            r = json.loads(line)
            if not r.get("draft_with_citations"):
                continue
            tb = LocalToolBox(r["persona"])
            pool = []
            for s in r["draft_with_citations"].split(". ")[:6]:
                res = await tb.call("portfolio", "search_evidence", {"requirement": CITE.sub("", s)[:300] or "x", "k": 3})
                pool += [{"doc_id": i["id"], "text": i["snippet"], "skills": i.get("skills", [])} for i in res["items"]]
            _, _, rep = await verify_text(r["draft_with_citations"], pool, use_llm=False)
            unsup.append(rep.unsupported_before)
    out = {"recall@5": mean(rec), "bid_f1": dec["bid_skip"]["f1"], "scam_recall": dec["scam"]["recall"],
           "unsupported_per_proposal": mean(unsup) if unsup else None, "n_jobs": len(dec_rows), "thresholds": th}
    fails = []
    if out["recall@5"] is not None and out["recall@5"] < th["recall5_min"]:
        fails.append(f"Recall@5 {out['recall@5']} < {th['recall5_min']}")
    if out["bid_f1"] < th["bid_f1_min"]:
        fails.append(f"bid/skip F1 {out['bid_f1']} < {th['bid_f1_min']}")
    if out["scam_recall"] < th["scam_recall_min"]:
        fails.append(f"scam recall {out['scam_recall']} < {th['scam_recall_min']}")
    if out["unsupported_per_proposal"] is not None and out["unsupported_per_proposal"] > th["unsupported_per_proposal_max"]:
        fails.append(f"unsupported claims {out['unsupported_per_proposal']} > {th['unsupported_per_proposal_max']}")
    out["failures"] = fails
    write_result("smoke_eval", out)
    print(json.dumps(out, indent=1))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
