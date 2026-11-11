"""SSE bridge: the worker streams LangGraph events (stream_mode custom + updates) into run_events; this endpoint
tails that table for one thread. Reconnects resume from Last-Event-ID, so a refresh never loses progress."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from apps.api import db
from apps.api.auth.users import CurrentUser

router = APIRouter(prefix="/api/runs", tags=["runs"])
TERMINAL = {"skipped", "watch", "submitted", "rejected", "blocked", "needs_attention", "dead_letter", "error",
            "sent", "done", "awaiting_approval"}


@router.get("/{thread_id}/stream")
async def stream(thread_id: str, request: Request, user=CurrentUser, last_event_id: str | None = Header(default=None),
                 after: int = 0):
    run = await db.fetchone("SELECT status FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user["user_id"])
    if not run:
        raise HTTPException(404, "run not found")
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    async def gen():
        cursor, idle = start, 0
        while True:
            if await request.is_disconnected():
                return
            rows = await db.fetch("SELECT id, node, kind, payload, created_at FROM run_events WHERE thread_id=%s AND "
                                  "user_id=%s AND id>%s ORDER BY id LIMIT 100", thread_id, user["user_id"], cursor)
            for r in rows:
                cursor = r["id"]
                yield {"id": str(r["id"]), "event": "node", "data": json.dumps(
                    {"id": r["id"], "node": r["node"], "kind": r["kind"], "payload": r["payload"],
                     "created_at": r["created_at"].isoformat()})}
            status = (await db.fetchone("SELECT status FROM runs WHERE thread_id=%s", thread_id))["status"]
            idle = 0 if rows else idle + 1
            if status in TERMINAL and idle >= 4:
                yield {"event": "status", "data": json.dumps({"status": status})}
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(gen(), ping=15)
