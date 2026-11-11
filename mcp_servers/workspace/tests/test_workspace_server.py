"""Workspace MCP server through an in-memory MCP client."""

from datetime import UTC, datetime, timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_slots_holds_and_drafts():
    from mcp_servers.workspace.server import mcp

    nb = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        slots = (await s.call_tool("list_free_slots", {"days_ahead": 5, "max_slots": 3,
                                                       "not_before": nb})).structuredContent
        assert len(slots["slots"]) == 3
        first = slots["slots"][0]
        h1 = await s.call_tool("create_interview_hold", {"start": first["start"], "end": first["end"], "title": "Interview",
                                                         "idempotency_key": "hold-key-1"})
        h2 = await s.call_tool("create_interview_hold", {"start": first["start"], "end": first["end"], "title": "Interview",
                                                         "idempotency_key": "hold-key-1"})
        assert h1.structuredContent["hold_id"] == h2.structuredContent["hold_id"]
        after = (await s.call_tool("list_free_slots", {"days_ahead": 5, "max_slots": 3,
                                                       "not_before": nb})).structuredContent
        assert first["start"] not in [x["start"] for x in after["slots"]]  # the held slot is no longer offered
        d = await s.call_tool("create_email_draft", {"to": "client", "subject": "Interview", "body": "See you then.",
                                                     "idempotency_key": "draft-key-1"})
        assert d.structuredContent["status"] == "draft"
