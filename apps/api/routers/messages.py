"""Client threads with suggested replies and interview slots; every send goes through the approval endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent import runner
from agent.mcp_client import MCPUnavailable, ToolBox
from apps.api import db
from apps.api.auth.users import CurrentUser
from apps.api.queue import enqueue
from apps.api.schemas.models import Thread

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("", response_model=list[Thread])
async def threads(user=CurrentUser):
    try:
        res = await ToolBox("pipeline_sync", user["user_id"], user["corpus"]).call("marketplace", "list_messages", {})
    except MCPUnavailable as e:
        raise HTTPException(503, "marketplace unavailable") from e
    convs = {r["marketplace_thread_id"]: r for r in await db.fetch(
        "SELECT marketplace_thread_id, graph_thread_id, status FROM conversations WHERE user_id=%s", user["user_id"])}
    titles = {r["job_id"]: r["job"].get("title") for r in await db.fetch(
        "SELECT job_id, job FROM inbox WHERE user_id=%s", user["user_id"])}
    out = []
    for t in res.get("items", []):
        c = convs.get(t["thread_id"])
        suggestion, gstatus = None, None
        if c and c["graph_thread_id"]:
            run = await db.fetchone("SELECT status FROM runs WHERE thread_id=%s", c["graph_thread_id"])
            gstatus = (run or {}).get("status")
            snap = await runner.snapshot("conversation", c["graph_thread_id"])
            if snap["interrupts"]:
                suggestion = snap["interrupts"][0]
        out.append(Thread(marketplace_thread_id=t["thread_id"], job_id=t.get("job_id"), job_title=titles.get(t.get("job_id")),
                          state=t.get("state"), messages=t.get("messages", []),
                          graph_thread_id=(c or {}).get("graph_thread_id"), graph_status=gstatus, suggestion=suggestion))
    return out


@router.post("/{marketplace_thread_id}/suggest")
async def suggest(marketplace_thread_id: str, user=CurrentUser) -> dict:
    """Draft a reply now (normally the worker does this when a client message arrives)."""
    queued = await enqueue("start_conversation", user["user_id"], marketplace_thread_id,
                           job_id=f"conv:{user['user_id']}:{marketplace_thread_id}:manual")
    return {"queued": queued}
