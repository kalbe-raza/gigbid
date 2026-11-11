"""Judge calibration (B4): the LLM judge is only trusted after it agrees with HUMAN labels.

Step 1 (done by the runner):  python -m evals.runners.judge_calibration prepare
    writes evals/datasets/judge_labels.csv with 30 real proposals from the GigSim outcome runs, the judge's
    score hidden, and an empty `human_label` column.
Step 2 (done by YOU, the student): fill `human_label` with good / ok / poor for all 30 rows (about 20 minutes).
    Rubric: good = specific to this client, concrete true evidence, would get a reply; poor = generic or unproven.
Step 3:  python -m evals.runners.judge_calibration score
    computes Cohen's kappa between your labels and the judge (overall score mapped >=7.5 good, >=5.5 ok, else poor),
    writes evals/results/judge_calibration.json and lists the disagreements to discuss in the viva.
No number in this file is ever produced without the human labels.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import sys

from agent.settings import ROOT
from evals.runners.common import RESULTS, write_result

LABELS = ROOT / "evals/datasets/judge_labels.csv"


def bucket(score: float) -> str:
    return "good" if score >= 7.5 else ("ok" if score >= 5.5 else "poor")


def kappa(a: list[str], b: list[str]) -> float:
    cats = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def prepare(n: int = 30) -> None:
    rows = []
    for f in sorted(RESULTS.glob("gigsim_outcomes_*_per_job.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r.get("text") and r.get("judge"):
                rows.append({"item_id": f"{f.stem.replace('_per_job', '')}:{r['job_id']}", "job_title": r["title"],
                             "proposal": r["text"], "judge_overall": r["judge"]["overall"]})
    random.Random(30).shuffle(rows)
    rows = rows[:n]
    if len(rows) < n:
        print(f"only {len(rows)} judged proposals available; run the GigSim outcome evals first")
    with LABELS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "job_title", "proposal", "human_label"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in ("item_id", "job_title", "proposal")} | {"human_label": ""})
    (ROOT / "evals/.cache").mkdir(exist_ok=True, parents=True)
    (ROOT / "evals/.cache/judge_scores.json").write_text(json.dumps({r["item_id"]: r["judge_overall"] for r in rows}))
    print(f"wrote {len(rows)} items to {LABELS}; fill the human_label column (good / ok / poor)")


def score() -> None:
    scores = json.loads((ROOT / "evals/.cache/judge_scores.json").read_text())
    rows = list(csv.DictReader(LABELS.open()))
    labelled = [r for r in rows if r["human_label"].strip().lower() in ("good", "ok", "poor")]
    if len(labelled) < len(rows):
        sys.exit(f"{len(rows) - len(labelled)} rows have no human_label yet - label all of them first")
    human = [r["human_label"].strip().lower() for r in labelled]
    judge = [bucket(scores[r["item_id"]]) for r in labelled]
    k = kappa(human, judge)
    dis = [{"item_id": r["item_id"], "human": h, "judge": j, "judge_score": scores[r["item_id"]]}
           for r, h, j in zip(labelled, human, judge, strict=True) if h != j]
    write_result("judge_calibration", {"n": len(labelled), "cohens_kappa": k,
                                       "agreement": round(1 - len(dis) / len(labelled), 4), "disagreements": dis,
                                       "mapping": ">=7.5 good, >=5.5 ok, else poor"})
    print(f"Cohen's kappa = {k} on {len(labelled)} items; {len(dis)} disagreements")


if __name__ == "__main__":
    {"prepare": prepare, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "prepare"]()
    _ = asyncio
