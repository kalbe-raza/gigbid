"""Human approval (C5) and exactly-once submission (C6).

dry_run         -> Marketplace MCP create_proposal_draft(dry_run=True): exact payload + Connects cost + balance after
human_approval  -> LangGraph interrupt(); payload = draft, evidence behind every claim, risk report, price rationale,
                   dry-run preview, critic + compliance notes. Resumed by the UI with approve / edit / reject.
apply_edit      -> diff the user's edit, feed style memory (promotion rules decide), new draft -> back to Compliance
submit          -> create_proposal_draft + submit_proposal with idempotency keys derived from (thread, draft version):
                   re-running after a crash returns the marketplace's stored response instead of submitting again.
                   Both calls carry the approval token the API signed for this exact content.
"""

from __future__ import annotations

import difflib
import re

from langgraph.types import interrupt

from agent.handoffs import ApprovalDecision
from agent.memory import store as memory
from agent.nodes.common import NodeContext, gigbid_node
from rag.verify import render_for_client


def idem_key(thread_id: str, version: int, step: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9:_.-]", "-", thread_id)[:100]
    return f"{safe}:v{version}:{step}"


def client_payload(state: dict) -> dict:
    """The exact text that will leave GigBid (evidence ids rendered as readable portfolio references)."""
    d, p = state["drafts"][-1], state["price"]
    titles = {e["doc_id"]: e.get("title", "") for e in state.get("evidence") or []}
    return {"job_id": state["job"]["job_id"], "cover_letter": render_for_client(d["cover_letter"], titles),
            "bid_amount": p["amount"], "bid_type": p["bid_type"],
            "screening_answers": [f"{a['question']}\n{render_for_client(a['answer'], titles)}"
                                  for a in d.get("screening_answers", [])]}


def claims_with_evidence(state: dict) -> list[dict]:
    ver = (state.get("verification") or [{}])[-1]
    ev = {e["doc_id"]: e for e in state.get("evidence") or []}
    out = []
    for c in ver.get("claims", []):
        if c.get("action") == "removed":
            continue
        out.append({"text": c["text"], "status": c["status"], "action": c.get("action"),
                    "evidence": [{"doc_id": i, "title": ev.get(i, {}).get("title", ""),
                                  "snippet": (ev.get(i, {}).get("snippet") or ev.get(i, {}).get("text", ""))[:400]}
                                 for i in c.get("evidence_ids", [])]})
    return out


@gigbid_node("dry_run")
async def dry_run(state: dict, ctx: NodeContext) -> dict:
    d = state["drafts"][-1]
    preview = await ctx.tools("dry_run").call("marketplace", "create_proposal_draft", client_payload(state) | {
        "idempotency_key": idem_key(ctx.thread_id or "t", d["version"], "preview"), "dry_run": True})
    return {"dry_run": preview, "_summary": f"preview: {preview.get('connects_cost')} Connects, "
                                            f"balance after {preview.get('connects_balance_after')}"}


@gigbid_node("human_approval")
async def human_approval(state: dict, ctx: NodeContext) -> dict:
    d = state["drafts"][-1]
    payload = {
        "kind": "proposal", "thread_id": ctx.thread_id, "job": {k: state["job"].get(k) for k in
                                                                ("job_id", "title", "category", "budget", "is_hourly",
                                                                 "hourly_low", "hourly_high")},
        "draft": d, "client_text": client_payload(state), "claims": claims_with_evidence(state), "risk": state.get("risk"), "price": state.get("price"),
        "dry_run": state.get("dry_run"), "critique": (state.get("critiques") or [None])[-1],
        "compliance": (state.get("compliance") or [None])[-1], "fit": {"score": state["fit"]["score"],
                                                                      "matrix": state["fit"]["matrix"]},
        "connects_spend": (state.get("dry_run") or {}).get("connects_cost"),
    }
    ctx.emit("awaiting_approval", draft_version=d["version"])
    raw = interrupt(payload)
    decision = ApprovalDecision.model_validate({k: v for k, v in (raw or {}).items() if k != "approval_token"})
    record = decision.model_dump() | {"draft_version": d["version"], "approval_token": (raw or {}).get("approval_token")}
    status = {"approve": "approved", "edit": "editing", "reject": "rejected"}[decision.decision]
    return {"approvals": [record], "status": status, "_summary": f"human: {decision.decision}"}


@gigbid_node("apply_edit")
async def apply_edit(state: dict, ctx: NodeContext) -> dict:
    a = state["approvals"][-1]
    prev = state["drafts"][-1]
    edited = (a.get("edited_text") or "").strip()
    diff = list(difflib.unified_diff(prev["cover_letter"].splitlines(), edited.splitlines(), "draft", "edited", lineterm=""))
    promoted = await memory.observe_edit(ctx.user_id, state["job"]["job_id"], prev["cover_letter"], edited)
    new = dict(prev) | {"version": prev["version"] + 1, "cover_letter": edited, "source": "human_edit",
                        "word_count": len(edited.split())}
    return {"drafts": [new], "status": "compliance", "exec_trace": [{"kind": "edit_diff", "diff": diff[:200],
                                                                     "promoted_rules": promoted}],
            "_summary": f"edit saved ({len(diff)} diff lines); {len(promoted)} style rule(s) promoted"}


@gigbid_node("submit")
async def submit(state: dict, ctx: NodeContext) -> dict:
    d = state["drafts"][-1]
    approval = state["approvals"][-1]
    token = approval.get("approval_token")
    tb = ctx.tools("submitter")
    draft = await tb.call("marketplace", "create_proposal_draft", client_payload(state) | {
        "idempotency_key": idem_key(ctx.thread_id or "t", d["version"], "draft"), "approval_token": token})
    res = await tb.call("marketplace", "submit_proposal", {
        "proposal_id": draft["proposal_id"], "idempotency_key": idem_key(ctx.thread_id or "t", d["version"], "submit"),
        "approval_token": token})
    return {"submission": res | {"draft_version": d["version"]}, "status": "submitted",
            "_summary": f"{res.get('status')} ({res.get('connects_spent', 0)} Connects)"}


@gigbid_node("finalize")
async def finalize(state: dict, ctx: NodeContext) -> dict:
    return {"_summary": f"final status: {state.get('status')}"}
