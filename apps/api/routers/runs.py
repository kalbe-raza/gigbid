"""Agent runs: node-by-node events, trace links, checkpoint history and replay from any checkpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent import runner
from agent.tracing import trace_url
from apps.api import db
from apps.api.auth.users import CurrentUser
from apps.api.queue import enqueue
from apps.api.schemas.models import CheckpointOut, RunEvent, RunOut

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _run(r: dict, events: list[dict] | None = None) -> RunOut:
    return RunOut(thread_id=r["thread_id"], kind=r["kind"], ref=r["ref"], status=r["status"], trace_id=r["trace_id"],
                  trace_url=trace_url(r["trace_id"]), usage=r["usage"] or {}, error=r["error"],
                  created_at=r["created_at"].isoformat(),
                  events=[RunEvent(id=e["id"], node=e["node"], kind=e["kind"], payload=e["payload"] or {},
                                   created_at=e["created_at"].isoformat()) for e in (events or [])])


@router.get("", response_model=list[RunOut])
async def list_runs(user=CurrentUser, limit: int = 50):
    rows = await db.fetch("SELECT * FROM runs WHERE user_id=%s ORDER BY updated_at DESC LIMIT %s", user["user_id"], limit)
    return [_run(r) for r in rows]


@router.get("/{thread_id}", response_model=RunOut)
async def get_run(thread_id: str, user=CurrentUser):
    r = await db.fetchone("SELECT * FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not r:
        raise HTTPException(404, "run not found")
    ev = await db.fetch("SELECT id, node, kind, payload, created_at FROM run_events WHERE thread_id=%s ORDER BY id", thread_id)
    return _run(r, ev)


@router.get("/{thread_id}/history", response_model=list[CheckpointOut])
async def checkpoint_history(thread_id: str, user=CurrentUser):
    r = await db.fetchone("SELECT kind FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not r:
        raise HTTPException(404, "run not found")
    return [CheckpointOut(**h) for h in await runner.history(r["kind"], thread_id)]


@router.post("/{thread_id}/replay/{checkpoint_id}")
async def replay(thread_id: str, checkpoint_id: str, user=CurrentUser) -> dict:
    r = await db.fetchone("SELECT kind FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not r:
        raise HTTPException(404, "run not found")
    queued = await enqueue("replay_thread", user["user_id"], thread_id, checkpoint_id,
                           job_id=f"replay:{thread_id}:{checkpoint_id}")
    return {"queued": queued}


@router.get("/{thread_id}/trace")
async def exec_trace(thread_id: str, user=CurrentUser) -> dict:
    """Execution trace kept in the checkpoint (tool calls, arguments, results), separate from state and memory."""
    r = await db.fetchone("SELECT kind FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not r:
        raise HTTPException(404, "run not found")
    v = (await runner.snapshot(r["kind"], thread_id))["values"]
    return {"exec_trace": v.get("exec_trace") or [], "usage": v.get("usage") or {}}
