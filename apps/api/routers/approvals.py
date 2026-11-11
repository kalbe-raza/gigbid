"""Approvals resume the interrupted graph thread (R4/C5).

The decision is persisted on the run (`pending_resume`) BEFORE the worker is asked to resume, so an API or worker
crash between "user clicked Approve" and "proposal submitted" is recovered by the worker's startup sweep; submission
is idempotent, so the proposal is submitted exactly once. For Approve, the API signs the exact text in the dry-run
preview; the Marketplace MCP server rejects anything else.
"""

from __future__ import annotations

import difflib

from fastapi import APIRouter, HTTPException

from agent import runner
from apps.api import db
from apps.api.auth import approval_tokens
from apps.api.auth.users import CurrentUser
from apps.api.queue import enqueue
from apps.api.schemas.models import ApprovalIn, ApprovalOut, PendingApproval

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=list[PendingApproval])
async def pending(user=CurrentUser):
    rows = await db.fetch("SELECT thread_id, kind, ref, updated_at FROM runs WHERE user_id=%s AND status='awaiting_approval' "
                          "ORDER BY updated_at DESC", user["user_id"])
    out = []
    for r in rows:
        title = None
        if r["kind"] == "bid":
            j = await db.fetchone("SELECT job FROM inbox WHERE user_id=%s AND job_id=%s", user["user_id"], r["ref"])
            title = (j or {}).get("job", {}).get("title")
        out.append(PendingApproval(thread_id=r["thread_id"], kind="proposal" if r["kind"] == "bid" else "message",
                                   title=title, ref=r["ref"], created_at=r["updated_at"].isoformat()))
    return out


@router.post("/{thread_id}", response_model=ApprovalOut)
async def decide(thread_id: str, body: ApprovalIn, user=CurrentUser):
    run = await db.fetchone("SELECT kind, ref, status FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not run:
        raise HTTPException(404, "run not found")
    snap = await runner.snapshot(run["kind"], thread_id)
    if not snap["interrupts"]:
        raise HTTPException(409, f"nothing is waiting for approval (status {run['status']})")
    payload = snap["interrupts"][0]
    if body.decision == "edit" and not (body.edited_text or "").strip():
        raise HTTPException(400, "edited_text is required for an edit")
    if body.decision == "reject" and not (body.reason or "").strip():
        raise HTTPException(400, "a reason is required when rejecting")
    value = {"decision": body.decision, "edited_text": body.edited_text, "reason": body.reason, "user_id": user["user_id"]}
    diff = None
    if body.decision == "approve":
        if payload["kind"] == "proposal":
            sent = (payload.get("dry_run") or {}).get("would_send")
            if not sent:
                raise HTTPException(409, "no dry-run preview to approve")
            chash = approval_tokens.proposal_hash(sent["job_id"], sent["cover_letter"], sent["bid_amount"], sent["bid_type"])
            value["approval_token"] = approval_tokens.sign(user["user_id"], "proposal", chash)
        else:
            text = payload["reply"]["text"]
            value["approval_token"] = approval_tokens.sign(
                user["user_id"], "message", approval_tokens.message_hash(payload["marketplace_thread_id"], text))
    elif body.decision == "edit":
        before = payload["draft"]["cover_letter"] if payload["kind"] == "proposal" else payload["reply"]["text"]
        diff = list(difflib.unified_diff(before.splitlines(), body.edited_text.splitlines(), lineterm=""))
    await db.execute("INSERT INTO approvals_log (user_id, thread_id, kind, decision, reason, diff) VALUES (%s,%s,%s,%s,%s,%s)",
                     user["user_id"], thread_id, payload["kind"], body.decision, body.reason, db.J(diff or []))
    await db.execute("UPDATE runs SET status='resuming', pending_resume=%s, updated_at=now() WHERE thread_id=%s",
                     db.J(value), thread_id)
    n = (await db.fetchone("SELECT count(*) AS n FROM approvals_log WHERE thread_id=%s", thread_id))["n"]
    await enqueue("resume_thread", user["user_id"], thread_id, job_id=f"resume:{thread_id}:{n}")
    return ApprovalOut(thread_id=thread_id, accepted=True, status="resuming")
