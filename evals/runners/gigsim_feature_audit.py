"""Audit of the GigSim 1.2.1 -> 1.2.2 change: the simulator's must-have feature for every proposal of the superseded
1.2.1 run, computed with the old exact-phrase rule and with the current token/stem rule.

    python -m evals.runners.gigsim_feature_audit   -> evals/results/gigsim_feature_audit.json
"""

from __future__ import annotations

import json

import gigsim.app as g
from agent.settings import ROOT
from evals.runners.common import RESULTS, write_result


def exact_hit(must: list[str], letter: str) -> float:
    low = letter.lower()
    return (sum(m.lower() in low for m in must) / len(must)) if must else 1.0


def main() -> None:
    jobs = {j["job_id"]: j for j in json.loads((ROOT / "gigsim/seed/jobs.json").read_text())}
    clients = json.loads((ROOT / "gigsim/seed/clients.json").read_text())  # {client_id: client}
    rows = []
    for line in (RESULTS / "superseded_gigsim121_best_openb_per_job.jsonl").read_text().splitlines():
        r = json.loads(line)
        if not r.get("text"):
            continue
        job = jobs[r["job_id"]]
        client = clients[job["client_id"]]
        must = client["hidden"].get("must_have") or []
        new = g.proposal_features(job, client, {"cover_letter": r["text"], "bid_amount": r.get("bid_amount")})
        rows.append({"job_id": r["job_id"], "must_have": must, "decision_121": (r.get("gigsim") or {}).get("decision"),
                     "must_have_hit_exact_121": round(exact_hit(must, r["text"]), 2),
                     "must_have_hit_stem_122": round(new["must_have_hit"], 2)})
    write_result("gigsim_feature_audit", {
        "proposals": len(rows),
        "zero_must_have_exact_121": sum(x["must_have_hit_exact_121"] == 0 for x in rows),
        "zero_must_have_stem_122": sum(x["must_have_hit_stem_122"] == 0 for x in rows),
        "mean_must_have_exact_121": round(sum(x["must_have_hit_exact_121"] for x in rows) / max(1, len(rows)), 3),
        "mean_must_have_stem_122": round(sum(x["must_have_hit_stem_122"] for x in rows) / max(1, len(rows)), 3),
        "rows": rows})
    for x in rows:
        print(x)


if __name__ == "__main__":
    main()
