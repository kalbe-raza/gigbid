"""B4 outcome + proposal quality: GigSim simulated reply rate on a FIXED job set, unsupported claims per proposal
(before/after verification), LLM-judge rubric score, cost and latency per proposal.

The real compiled bid graph runs to the approval interrupt (tools answered in-process). The exact client-facing text
from the dry run is then submitted to a GigSim instance whose simulated clients decide with an LLM
(GIGSIM_CLIENT_MODE=llm), and the decision (ignored / replied / interview) is read back.

    RAG_VARIANT=best MODEL_PROFILE=open python -m evals.runners.gigsim_outcomes --per-persona 6 --tag best_open
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid

os.environ["GIGBID_LOCAL_TOOLS"] = "1"

import httpx  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.graphs.bid_graph import build_bid_graph  # noqa: E402
from agent.llm import Ledger, LLMError, chat, current_ledger, load_prompt  # noqa: E402
from agent.memory.store import set_store  # noqa: E402
from agent.settings import ROOT, settings  # noqa: E402
from evals.runners.common import (  # noqa: E402
    PERSONAS,
    RESULTS,
    build_indexes,
    dev,
    mean,
    pctl,
    register_jobs,
    sample,
    wilson,
    write_result,
)

GIGSIM = os.getenv("GIGSIM_EVAL_URL", "http://localhost:8782")
TOKEN = os.getenv("GIGSIM_EVAL_TOKEN", "gs-eval-token")
ADMIN = os.getenv("GIGSIM_ADMIN_TOKEN", "gigsim-admin")


def fixed_job_set(per_persona: int, split: str = "dev") -> dict[str, list[dict]]:
    """Gold-bid, real, non-injected jobs; seeded so every configuration sees exactly the same jobs."""
    return {p: sample(dev(p, split), per_persona, seed=2024,
                      key=lambda r: r["gold"]["decision"] == "bid" and r["gold"]["kind"] == "real_in_domain")
            for p in PERSONAS}


async def submit_to_gigsim(would_send: dict) -> dict:
    h = {"Authorization": f"Bearer {TOKEN}", "x-gigsim-no-chaos": "1"}
    async with httpx.AsyncClient(timeout=90) as c:
        await c.post(f"{GIGSIM}/admin/reset", params={"user_id": "eval"}, headers={"x-admin-token": ADMIN})
        key = uuid.uuid4().hex
        d = await c.post(f"{GIGSIM}/v1/proposals", headers=h | {"Idempotency-Key": key + "d"}, json={
            "job_id": would_send["job_id"], "cover_letter": would_send["cover_letter"],
            "screening_answers": would_send.get("screening_answers", []), "bid_amount": would_send["bid_amount"],
            "bid_type": would_send["bid_type"]})
        if d.status_code != 200:
            return {"status": "rejected_by_platform", "detail": d.text[:200]}
        pid = d.json()["proposal_id"]
        s = await c.post(f"{GIGSIM}/v1/proposals/{pid}/submit", headers=h | {"Idempotency-Key": key + "s"})
        if s.status_code != 200:
            return {"status": "rejected_by_platform", "detail": s.text[:200]}
        await c.post(f"{GIGSIM}/admin/fast_forward", params={"seconds": 3600}, headers={"x-admin-token": ADMIN})
        o = await c.get(f"{GIGSIM}/admin/outcomes", params={"user_id": "eval"}, headers={"x-admin-token": ADMIN})
        row = next(x for x in o.json()["items"] if x["proposal_id"] == pid)
        decision = json.loads(row["decision"])[0] if row["decision"] else "unknown"
        return {"status": row["status"], "decision": decision, "mode": s.json().get("decision_mode")}


class JudgeOut(BaseModel):
    specificity: int
    evidence: int
    honesty: int
    clarity: int
    persuasiveness: int
    overall: float
    rationale: str = ""


async def judge(job: dict, text: str) -> dict | None:
    try:
        res = await chat("judge", [{"role": "system", "content": load_prompt("judge")},
                                   {"role": "user", "content": f"JOB: {job['title']}\n{job['description'][:1500]}\n\n"
                                                               f"PROPOSAL:\n{text}"}], schema=JudgeOut, max_tokens=400)
        return res.parsed.model_dump()
    except LLMError:
        return None


async def run_one(graph, row: dict, persona: str, profile: dict, extra: dict | None = None) -> dict:
    led = Ledger()
    tok = current_ledger.set(led)
    t0 = time.perf_counter()
    tid = f"eval-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}}
    out = {"persona": persona, "job_id": row["job"]["job_id"], "title": row["job"]["title"]}
    try:
        state = {"thread_id": tid, "user_id": "eval", "corpus": persona, "trace_id": uuid.uuid4().hex, "profile": profile,
                 "job": {"job_id": row["job"]["job_id"]}, "autonomy": "manual", "steps": 0, **(extra or {})}
        await graph.ainvoke(state, cfg)
        snap = await graph.aget_state(cfg)
        v = snap.values
        out.update(decision=(v.get("decision") or {}).get("action"), status=v.get("status"), arm=v.get("experiment_arm"),
                   interrupted=bool(snap.next), latency_s=round(time.perf_counter() - t0, 2))
        if snap.next and v.get("dry_run"):
            sent = v["dry_run"]["would_send"]
            ver = v["verification"]
            out.update(text=sent["cover_letter"], draft_with_citations=v["drafts"][-1]["cover_letter"],
                       unsupported_before=ver[0]["unsupported_before"], unsupported_first_draft=ver[0]["unsupported_before"],
                       unsupported_after=ver[-1]["unsupported_after"], claims=ver[-1]["total_claims"],
                       critic_rounds=len(v.get("critiques") or []), revisions=v.get("revision", 0),
                       words=len(sent["cover_letter"].split()), bid_amount=sent["bid_amount"])
            out["judge"] = await judge(row["job"], sent["cover_letter"])
            out["gigsim"] = await submit_to_gigsim(sent)
    except Exception as e:  # noqa: BLE001 - record and continue: a failed job is a result too
        out.update(error=f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        out.update(tokens=led.tokens, cost_usd=led.cost, nodes=led.by_node(), latency_s=round(time.perf_counter() - t0, 2),
                   llm_calls=len(led.calls), cached_calls=sum(c.cached for c in led.calls))
        current_ledger.reset(tok)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=6)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="test = the held-out split (report only)")
    a = ap.parse_args()
    from langgraph.store.memory import InMemoryStore

    set_store(InMemoryStore())
    build_indexes(schemes=("structured", "fixed"))
    jobs = fixed_job_set(a.per_persona, a.split)
    for rows in jobs.values():
        register_jobs(rows)
    graph = build_bid_graph(InMemorySaver())
    results = []
    for p, rows in jobs.items():
        profile = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text()) | {"domain": p}
        for r in rows:
            res = await run_one(graph, r, p, profile)
            results.append(res)
            print(p, res["job_id"], res.get("decision"), (res.get("gigsim") or {}).get("decision"),
                  res.get("error", ""), f"{res['tokens']} tok", flush=True)
    props = [r for r in results if r.get("gigsim") and r["gigsim"].get("decision")]
    replied = [r for r in props if r["gigsim"]["decision"] in ("replied", "interview")]
    judged = [r["judge"]["overall"] for r in props if r.get("judge")]
    summary = {
        "jobs": len(results), "proposals": len(props), "errors": sum(1 for r in results if r.get("error")),
        "no_bid": sum(1 for r in results if r.get("decision") in ("skip", "watch")),
        "reply_rate": round(len(replied) / len(props), 4) if props else None,
        "reply_ci95": wilson(len(replied), len(props)),
        "interview_rate": mean([r["gigsim"]["decision"] == "interview" for r in props]),
        "client_mode": sorted({r["gigsim"].get("mode") for r in props if r["gigsim"].get("mode")}),
        "unsupported_claims_per_proposal_before": mean(r["unsupported_first_draft"] for r in props),
        "unsupported_claims_per_proposal_after": mean(r["unsupported_after"] for r in props),
        "claims_per_proposal": mean(r["claims"] for r in props),
        "unsupported_rate_before": round(sum(r["unsupported_first_draft"] for r in props) /
                                         max(1, sum(r["claims"] for r in props)), 4) if props else None,
        "judge_overall_mean": mean(judged),
        "critic_rounds_mean": mean(r["critic_rounds"] for r in props),
        "tokens_per_proposal": mean(r["tokens"] for r in props),
        "usd_per_proposal": mean(r["cost_usd"] for r in props),
        # LLM calls answered from the eval cache take 0 s, so latency is only a fair measurement when this is ~0
        "cached_call_share": round(sum(r.get("cached_calls", 0) for r in props) /
                                   max(1, sum(r.get("llm_calls", 0) for r in props)), 3) if props else None,
        "latency_p50_s": pctl([r["latency_s"] for r in props], .5),
        "latency_p95_s": pctl([r["latency_s"] for r in props], .95),
        "by_persona": {p: {"proposals": len([r for r in props if r["persona"] == p]),
                           "reply_rate": mean([r["gigsim"]["decision"] in ("replied", "interview") for r in props
                                               if r["persona"] == p])} for p in PERSONAS},
    }
    write_result(f"gigsim_outcomes_{a.tag}", {"rag_variant": settings().rag["variant"],
                                               "model_profile": os.getenv("MODEL_PROFILE", settings().models["profile"]),
                                               "writer_model": settings().model_for("writer"),
                                               "per_persona": a.per_persona, "split": a.split, "summary": summary})
    (RESULTS / f"gigsim_outcomes_{a.tag}_per_job.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in results))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
