"""X2 signature extension: proposal A/B experiment in GigSim with a significance test on reply rate.

Paired design: every job in the set is written twice, once per strategy (agent/prompts/writer_strategy_{A,B}.md),
and both proposals go to GigSim's LLM-driven simulated clients. Pairing removes job-to-job variance, so McNemar's
exact test on the discordant pairs is the primary test; a two-proportion z-test is reported for comparison.

    MODEL_PROFILE=open_b python -m evals.runners.ab_test --per-persona 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os

os.environ["GIGBID_LOCAL_TOOLS"] = "1"

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402

from agent.graphs.bid_graph import build_bid_graph  # noqa: E402
from agent.memory.store import set_store  # noqa: E402
from agent.settings import ROOT, settings  # noqa: E402
from evals.runners.common import RESULTS, build_indexes, mean, register_jobs, wilson, write_result  # noqa: E402
from evals.runners.gigsim_outcomes import fixed_job_set, run_one  # noqa: E402


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant pairs b (A replied, B not) and c (B replied, A not)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    if not n1 or not n2:
        return 0.0, 1.0
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) or 1e-9
    z = (k1 / n1 - k2 / n2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=4)
    a = ap.parse_args()
    set_store(InMemoryStore())
    build_indexes(schemes=("structured", "fixed"))
    jobs = fixed_job_set(a.per_persona)
    for rows in jobs.values():
        register_jobs(rows)
    graph = build_bid_graph(InMemorySaver())
    pairs = []
    for p, rows in jobs.items():
        profile = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text()) | {"domain": p}
        for r in rows:
            res = {}
            for arm in ("A", "B"):
                res[arm] = await run_one(graph, r, p, profile, {"experiment_arm": arm})
            ok = all((res[x].get("gigsim") or {}).get("decision") for x in "AB")
            pairs.append({"persona": p, "job_id": r["job"]["job_id"], "complete": ok,
                          **{f"{x}_{k}": (res[x].get("gigsim") or {}).get("decision") if k == "decision" else res[x].get(k)
                             for x in "AB" for k in ("decision", "words", "tokens", "judge", "unsupported_after")}})
            print(p, r["job"]["job_id"], pairs[-1]["A_decision"], pairs[-1]["B_decision"], flush=True)
    done = [x for x in pairs if x["complete"]]
    rep = lambda d: d in ("replied", "interview")  # noqa: E731
    kA, kB = sum(rep(x["A_decision"]) for x in done), sum(rep(x["B_decision"]) for x in done)
    b = sum(rep(x["A_decision"]) and not rep(x["B_decision"]) for x in done)
    c = sum(rep(x["B_decision"]) and not rep(x["A_decision"]) for x in done)
    z, pz = two_prop_z(kA, len(done), kB, len(done))
    summary = {"pairs": len(done), "reply_rate_A": round(kA / len(done), 4) if done else None,
               "reply_rate_B": round(kB / len(done), 4) if done else None,
               "ci95_A": wilson(kA, len(done)), "ci95_B": wilson(kB, len(done)),
               "discordant_A_only": b, "discordant_B_only": c, "mcnemar_exact_p": round(mcnemar_exact(b, c), 4),
               "two_proportion_z": round(z, 3), "two_proportion_p": round(pz, 4),
               "words_A": mean(x["A_words"] for x in done), "words_B": mean(x["B_words"] for x in done),
               "tokens_A": mean(x["A_tokens"] for x in done), "tokens_B": mean(x["B_tokens"] for x in done),
               "significant_at_0.05": mcnemar_exact(b, c) < 0.05,
               "writer_model": settings().model_for("writer")}
    write_result("ab_test", {"design": "paired, both strategies per job, LLM-driven GigSim clients", "summary": summary})
    (RESULTS / "ab_test_pairs.jsonl").write_text("".join(json.dumps(x, default=str) + "\n" for x in pairs))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
