"""Failure analysis (report section 8): failed dev jobs grouped into a taxonomy, from the result files only.

    python -m evals.runners.failure_analysis   -> evals/results/failure_analysis.json

Taxonomy (from the assignment): missed evidence, wrong skip, wrong bid, mispriced, unsupported claim, generic
opening, compliance miss, injection, tool error. Each failure keeps its job id so it can be opened in the dev set
and replayed. Sources:
  decisions_*_per_job.jsonl          bid/skip errors and prices outside the fair band
  gigsim_outcomes_*_per_job.jsonl    unsupported claims in first drafts, generic openings, tool errors, ignored
  injection_asr.json                 attacks that still succeeded after the defences
  rnd_c_per_job.jsonl                policy violations left in the graph's final letters
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from evals.runners.common import PERSONAS, RESULTS, dev, write_result

GENERIC = re.compile(r"^(dear (sir|madam|hiring)|hello,? i am|hi there!? i am|i am (very )?passionate|"
                     r"i am an expert|greetings|to whom it may concern|i have read your job)", re.I)


def _jsonl(name: str) -> list[dict]:
    f = RESULTS / name
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def main() -> None:
    gold = {r["job"]["job_id"]: r for p in PERSONAS for r in dev(p)}
    groups: dict[str, list[dict]] = defaultdict(list)

    decision_files = sorted(f for f in RESULTS.glob("decisions_*_per_job.jsonl") if "_test_" not in f.name)
    dec = _jsonl(decision_files[0].name) if decision_files else []
    for r in dec:
        g = gold.get(r["job_id"], {}).get("gold", {})
        base = {"job_id": r["job_id"], "persona": r["persona"], "kind": r["kind"],
                "title": gold.get(r["job_id"], {}).get("job", {}).get("title", ""), "source": decision_files[0].name}
        if r["gold"] == "bid" and r["pred"] != "bid":
            # evidence existed for the job's skills but the matcher's score stayed under the bid threshold
            cat = "missed evidence" if g.get("relevant_evidence") and r["fit"] < 0.5 else "wrong skip"
            groups[cat].append(base | {"detail": f"gold bid, predicted {r['pred']} (fit {r['fit']:.2f}); "
                                                 f"reasons: {'; '.join(r.get('reasons') or [])[:200]}"})
        elif r["gold"] != "bid" and r["pred"] == "bid":
            why = "scam not caught" if r["gold_scam"] else f"gold {r['gold']}"
            groups["wrong bid"].append(base | {"detail": f"{why}, predicted bid (fit {r['fit']:.2f}, risk {r['risk']:.2f})"})
        if r.get("price_in_band") is False:
            band = g.get("fair_price", {})
            groups["mispriced"].append(base | {"detail": f"quoted {r['price']} vs fair band {band.get('low')}-"
                                                         f"{band.get('high')} ({band.get('type')})"})

    for f in sorted(RESULTS.glob("gigsim_outcomes_*_per_job.jsonl")):
        for r in _jsonl(f.name):
            base = {"job_id": r["job_id"], "persona": r["persona"], "title": r.get("title", ""), "source": f.name}
            if r.get("error"):
                groups["tool error"].append(base | {"detail": r["error"][:200]})
                continue
            if r.get("unsupported_first_draft"):
                groups["unsupported claim"].append(base | {
                    "detail": f"{r['unsupported_first_draft']} unsupported claim(s) in the first draft, "
                              f"{r.get('unsupported_after', 0)} left after verification"})
            if r.get("text") and GENERIC.search(r["text"].strip()[:120]):
                groups["generic opening"].append(base | {"detail": r["text"][:120]})

    inj = RESULTS / "injection_asr.json"
    if inj.exists():
        for r in json.loads(inj.read_text()).get("cases", []):
            if r.get("success_after"):
                groups["injection"].append({"job_id": r["job_id"], "source": inj.name,
                                            "detail": f"{r.get('type')}: attack succeeded after the defences; "
                                                      f"output: {r.get('defended_excerpt', '')[:120]}"})

    for r in _jsonl("rnd_c_per_job.jsonl"):
        if r.get("arch") == "graph" and r.get("violations_left"):
            groups["compliance miss"].append({"job_id": r["job_id"], "persona": r.get("persona"),
                                              "source": "rnd_c_per_job.jsonl",
                                              "detail": f"violations left in the final text: {r['violations_left']}"})

    order = ["missed evidence", "wrong skip", "wrong bid", "mispriced", "unsupported claim", "generic opening",
             "compliance miss", "injection", "tool error"]
    summary = {k: len(groups.get(k, [])) for k in order}
    unique_jobs = {e["job_id"] for v in groups.values() for e in v}
    write_result("failure_analysis", {"counts": summary, "unique_failed_jobs": len(unique_jobs),
                                      "examples": {k: groups.get(k, [])[:8] for k in order}})
    for k in order:
        print(f"{k:<18} {summary[k]}")
    print("unique failed jobs:", len(unique_jobs))


if __name__ == "__main__":
    main()
