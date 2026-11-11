"""End-to-end smoke run of the bid graph on one GigSim job (A: "a single agent drafting one grounded proposal").

    python scripts/smoke_bid.py --persona fullstack [--job-id job-...] [--approve]

Runs intake -> parallel analysis -> decide -> pricing -> writer -> verifier -> critic -> compliance -> dry_run and stops
at the human-approval interrupt. With --approve it signs an approval exactly like the UI does and resumes, so the
proposal is drafted and submitted on GigSim once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graphs.bid_graph import build_bid_graph
from agent.llm import Ledger, current_ledger
from agent.settings import ROOT
from agent.tracing import init_tracing, trace_root, trace_url
from apps.api.auth import approval_tokens


def pick_job(persona: str, job_id: str | None) -> dict:
    rows = [json.loads(line) for line in open(ROOT / f"evals/datasets/dev_{persona}.jsonl")]
    if job_id:
        return next(r for r in rows if r["job"]["job_id"] == job_id)
    return next(r for r in rows if r["gold"]["decision"] == "bid" and r["gold"]["kind"] == "real_in_domain"
                and r["job"].get("budget"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="fullstack")
    ap.add_argument("--job-id")
    ap.add_argument("--approve", action="store_true")
    a = ap.parse_args()
    init_tracing("smoke")
    row = pick_job(a.persona, a.job_id)
    profile = json.loads((ROOT / f"data/personas/{a.persona}/profile.json").read_text()) | {"domain": a.persona}
    graph = build_bid_graph(checkpointer=InMemorySaver())
    tid = f"smoke-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}}
    ledger = Ledger()
    current_ledger.set(ledger)
    with trace_root("bid_graph.smoke", kind="CHAIN") as trace_id:
        state = {"thread_id": tid, "user_id": "demo", "corpus": a.persona, "trace_id": trace_id, "profile": profile,
                 "job": {"job_id": row["job"]["job_id"]}, "autonomy": "manual"}
        async for ev in graph.astream(state, cfg, stream_mode=["custom"]):
            _, data = ev
            if data.get("kind") == "finished":
                print(f"  {data['node']:<15} {data.get('summary')}")
        snap = await graph.aget_state(cfg)
        import os

        if os.getenv("SMOKE_DUMP"):
            open(os.environ["SMOKE_DUMP"], "w").write(json.dumps(snap.values, indent=1, default=str))
        print("status:", snap.values.get("status"), "| next:", snap.next, "| gold:", row["gold"]["decision"])
        if snap.next and snap.next[0] == "human_approval":
            d = snap.values["drafts"][-1]
            print(f"\n--- draft v{d['version']} ({d['word_count']} words) ---\n{d['cover_letter']}\n")
            ver = snap.values["verification"][-1]
            print("claims:", ver["total_claims"], "unsupported before/after:", ver["unsupported_before"], ver["unsupported_after"])
            print("dry run:", json.dumps({k: snap.values["dry_run"].get(k) for k in ("connects_cost", "connects_balance_after",
                                                                                     "policy_warnings")}))
            if a.approve:
                sent = snap.values["dry_run"]["would_send"]  # sign exactly what will be sent, as the API does
                chash = approval_tokens.proposal_hash(sent["job_id"], sent["cover_letter"], sent["bid_amount"], sent["bid_type"])
                token = approval_tokens.sign("demo", "proposal", chash)
                async for ev in graph.astream(Command(resume={"decision": "approve", "approval_token": token}), cfg,
                                              stream_mode=["custom"]):
                    _, data = ev
                    if data.get("kind") == "finished":
                        print(f"  {data['node']:<15} {data.get('summary')}")
                snap = await graph.aget_state(cfg)
                print("final:", snap.values.get("status"), snap.values.get("submission"))
    print(f"\ntokens {ledger.tokens}  cost ${ledger.cost:.5f}  trace {trace_url(trace_id) or trace_id}")
    print(json.dumps(ledger.by_node(), indent=1))


if __name__ == "__main__":
    asyncio.run(main())
