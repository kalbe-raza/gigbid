"""R8 / E5: declared budgets (agent/config.yaml) vs measured p95 latency and cost per job analysed / per proposal.

Reads only result files: decisions_*.json (per job analysed) and gigsim_outcomes_*.json (per proposal).
    python -m evals.runners.budget_report
"""

from __future__ import annotations

import json

from agent.settings import settings
from evals.runners.common import RESULTS, write_result


def main() -> None:
    declared = settings().budgets["declared"]
    measured: dict = {"per_job_analysed": {}, "per_proposal": {}}
    for f in sorted(RESULTS.glob("decisions_*.json")):
        if f.name.endswith("_per_job.jsonl") or "smoke" in f.name:
            continue
        d = json.loads(f.read_text())["overall"]["per_job"]
        measured["per_job_analysed"][f.stem] = {"p95_latency_s": d["latency_p95_s"], "usd_mean": d["usd_mean"],
                                               "tokens_mean": d["tokens_mean"]}
    for f in sorted(RESULTS.glob("gigsim_outcomes_*.json")):
        d = json.loads(f.read_text())["summary"]
        measured["per_proposal"][f.stem] = {"p95_latency_s": d["latency_p95_s"], "usd_mean": d["usd_per_proposal"],
                                           "tokens_mean": d["tokens_per_proposal"]}
    verdict = {}
    for k, m in measured["per_job_analysed"].items():
        verdict[k] = {"latency_ok": m["p95_latency_s"] is not None and m["p95_latency_s"] <= declared["p95_latency_s_per_job_analysed"],
                      "cost_ok": m["usd_mean"] is not None and m["usd_mean"] <= declared["usd_per_job_analysed"]}
    for k, m in measured["per_proposal"].items():
        verdict[k] = {"latency_ok": m["p95_latency_s"] is not None and m["p95_latency_s"] <= declared["p95_latency_s_per_proposal"],
                      "cost_ok": m["usd_mean"] is not None and m["usd_mean"] <= declared["usd_per_proposal"]}
    write_result("budgets", {"declared": declared, "measured": measured, "verdict": verdict,
                             "note": "USD is list-price equivalent (free tier was used, actual spend $0)."})
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
