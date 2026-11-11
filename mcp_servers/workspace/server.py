"""Workspace MCP server (A3): calendar slots, tentative interview holds, email drafts.

Backed by a documented LOCAL MOCK (Postgres tables workspace_holds / workspace_drafts, or memory without a DB),
not Google Calendar/Gmail: free accounts would need OAuth verification for a public demo. The agent only sees MCP
tools, so swapping in Google's APIs changes this file only (docs/adr/0005-workspace-mock.md).

Working hours default to Mon-Fri 10:00-18:00 Asia/Karachi and can be overridden per user in Settings.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuth, audit, identity

SERVER = "workspace"
TZ = ZoneInfo(os.getenv("WORKSPACE_TZ", "Asia/Karachi"))
mcp = FastMCP("gigbid-workspace", instructions="Calendar availability, tentative interview holds and email drafts.",
              host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("WORKSPACE_MCP_PORT", "8103")),
              stateless_http=True, json_response=True)
_MEM_HOLDS: dict[str, dict] = {}
_MEM_DRAFTS: dict[str, dict] = {}


class Slot(BaseModel):
    start: str = Field(description="ISO 8601 with timezone")
    end: str
    label: str = Field(description="Human-readable, e.g. 'Tue 12 Nov, 15:00-15:30 PKT'")


class SlotList(BaseModel):
    timezone: str
    slots: list[Slot]


class Hold(BaseModel):
    hold_id: str
    start: str
    end: str
    title: str
    status: str


class EmailDraft(BaseModel):
    draft_id: str
    to: str
    subject: str
    status: str = "draft"


async def _holds(user: str) -> list[dict]:
    if os.getenv("DATABASE_URL"):
        from apps.api import db

        return await db.fetch("SELECT start_at, end_at FROM workspace_holds WHERE user_id=%s AND end_at > now()", user)
    now = datetime.now(TZ)
    return [h for h in _MEM_HOLDS.values() if h["user_id"] == user and h["end_at"] > now]


def _label(s: datetime, e: datetime) -> str:
    return f"{s:%a %d %b, %H:%M}-{e:%H:%M} {s.tzname()}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def list_free_slots(ctx: Context, days_ahead: Annotated[int, Field(ge=1, le=14)] = 5,
                          duration_minutes: Annotated[int, Field(ge=15, le=120)] = 30,
                          max_slots: Annotated[int, Field(ge=1, le=10)] = 3,
                          not_before: str | None = None) -> SlotList:
    """Free interview slots in working hours (Mon-Fri 10:00-18:00 local), avoiding existing holds."""
    user, trace, _ = identity(ctx)
    now = datetime.fromisoformat(not_before).astimezone(TZ) if not_before else datetime.now(TZ)
    busy = [(h["start_at"], h["end_at"]) for h in await _holds(user)]
    slots: list[Slot] = []
    day = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    for _ in range(days_ahead + 4):
        if day.weekday() < 5:
            for hour in (11, 15, 17):  # spread across the day instead of back-to-back
                s = day.replace(hour=hour)
                e = s + timedelta(minutes=duration_minutes)
                if not any(bs < e and be > s for bs, be in busy):
                    slots.append(Slot(start=s.isoformat(), end=e.isoformat(), label=_label(s, e)))
                    break
        if len(slots) >= max_slots:
            break
        day += timedelta(days=1)
    await audit(SERVER, user, "list_free_slots", {"days_ahead": days_ahead}, {"n": len(slots)}, trace, True)
    return SlotList(timezone=str(TZ), slots=slots)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def create_interview_hold(ctx: Context, start: str, end: str,
                                title: Annotated[str, Field(min_length=3, max_length=120)],
                                idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9:_.-]{8,128}$")]) -> Hold:
    """Create a TENTATIVE calendar hold for an interview slot (same key -> same hold)."""
    user, trace, _ = identity(ctx)
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError as ex:
        raise ToolError("start/end must be ISO 8601") from ex
    if e <= s:
        raise ToolError("end must be after start")
    hid = "hold-" + hashlib.sha1(f"{user}{idempotency_key}".encode()).hexdigest()[:10]
    if os.getenv("DATABASE_URL"):
        from apps.api import db

        await db.execute("INSERT INTO workspace_holds (hold_id, user_id, start_at, end_at, title) VALUES "
                         "(%s,%s,%s,%s,%s) ON CONFLICT (hold_id) DO NOTHING", hid, user, s, e, title)
    else:
        _MEM_HOLDS.setdefault(hid, {"user_id": user, "start_at": s, "end_at": e, "title": title})
    await audit(SERVER, user, "create_interview_hold", {"start": start, "title": title}, {"hold_id": hid}, trace, True)
    return Hold(hold_id=hid, start=s.isoformat(), end=e.isoformat(), title=title, status="tentative")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def create_email_draft(ctx: Context, to: Annotated[str, Field(max_length=200)],
                             subject: Annotated[str, Field(min_length=1, max_length=200)],
                             body: Annotated[str, Field(min_length=1, max_length=8000)],
                             idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9:_.-]{8,128}$")]) -> EmailDraft:
    """Save an email DRAFT (never sends). Useful for interview confirmations outside the marketplace thread."""
    user, trace, _ = identity(ctx)
    did = "draft-" + hashlib.sha1(f"{user}{idempotency_key}".encode()).hexdigest()[:10]
    if os.getenv("DATABASE_URL"):
        from apps.api import db

        await db.execute("INSERT INTO workspace_drafts (draft_id, user_id, to_addr, subject, body) VALUES "
                         "(%s,%s,%s,%s,%s) ON CONFLICT (draft_id) DO NOTHING", did, user, to, subject, body)
    else:
        _MEM_DRAFTS.setdefault(did, {"user_id": user, "to": to, "subject": subject, "body": body})
    await audit(SERVER, user, "create_email_draft", {"to": to, "subject": subject}, {"draft_id": did}, trace, True)
    return EmailDraft(draft_id=did, to=to, subject=subject)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": SERVER})


def app():
    a = mcp.streamable_http_app()
    a.add_middleware(BearerAuth, env_var="MCP_SERVICE_TOKENS")
    return a


def main() -> None:
    import uvicorn

    from agent.tracing import init_tracing

    init_tracing("mcp-workspace")
    uvicorn.run(app(), host=mcp.settings.host, port=mcp.settings.port, log_level="warning")


if __name__ == "__main__":
    main()
