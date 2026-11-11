"""Marketplace MCP server through an in-memory MCP client, with a fake adapter behind the port."""

import os

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

os.environ["GIGSIM_API_TOKEN"] = "test"


class FakeAdapter:
    name = "fake"

    def __init__(self):
        self.drafts, self.submits = {}, []

    async def get_job(self, job_id):
        return {"job_id": job_id, "title": "T", "description": "D", "connects": {"base": 4, "cost": 6, "surcharges": ["high_value_job"]}}

    async def get_policy(self):
        return {"version": "2026-11-02", "connects": {}, "proposal": {"max_cover_letter_chars": 1500}}

    async def get_connects_balance(self):
        return {"balance": 20}

    async def create_proposal_draft(self, job_id, cover_letter, screening_answers, bid_amount, bid_type, idempotency_key):
        pid = self.drafts.setdefault(idempotency_key, f"pr-{len(self.drafts) + 1}")
        return {"proposal_id": pid, "status": "draft"}

    async def submit_proposal(self, proposal_id, idempotency_key):
        first = idempotency_key not in self.submits
        self.submits.append(idempotency_key)
        return {"proposal_id": proposal_id, "status": "submitted", "connects_spent": 6 if first else 0}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake(monkeypatch):
    import mcp_servers.marketplace.server as srv

    f = FakeAdapter()
    monkeypatch.setattr(srv, "make_adapter", lambda mode, creds: f)
    return f


@pytest.mark.anyio
async def test_schemas_annotations_and_dry_run(fake):
    from mcp_servers.marketplace.server import mcp

    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert tools["submit_proposal"].annotations.destructiveHint is True
        assert tools["get_job"].annotations.readOnlyHint is True
        assert tools["create_proposal_draft"].outputSchema is not None
        assert "idempotency_key" in tools["send_message"].inputSchema["required"]
        letter = "x" * 1600
        r = await s.call_tool("create_proposal_draft", {"job_id": "job-1", "cover_letter": letter, "bid_amount": 500,
                                                        "bid_type": "fixed", "idempotency_key": "k-12345678", "dry_run": True})
        sc = r.structuredContent
        assert sc["dry_run"] and sc["connects_cost"] == 6 and sc["connects_balance_after"] == 14
        assert sc["would_send"]["cover_letter"] == letter and sc["policy_warnings"]
        assert fake.drafts == {}  # nothing was sent


@pytest.mark.anyio
async def test_writes_need_human_approval_and_are_idempotent(fake):
    from apps.api.auth import approval_tokens
    from mcp_servers.marketplace.server import mcp

    args = {"job_id": "job-1", "cover_letter": "A proposal long enough to pass the minimum length check.",
            "bid_amount": 500, "bid_type": "fixed", "idempotency_key": "k-abcdefgh"}
    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        denied = await s.call_tool("create_proposal_draft", args)
        assert denied.isError and "approval_required" in denied.content[0].text
        token = approval_tokens.sign("local", "proposal", approval_tokens.proposal_hash("job-1", args["cover_letter"], 500, "fixed"))
        ok = await s.call_tool("create_proposal_draft", args | {"approval_token": token})
        pid = ok.structuredContent["proposal_id"]
        again = await s.call_tool("create_proposal_draft", args | {"approval_token": token})
        assert again.structuredContent["proposal_id"] == pid  # same key -> same draft
        sub = {"proposal_id": pid, "idempotency_key": "s-abcdefgh", "approval_token": token}
        r1 = await s.call_tool("submit_proposal", sub)
        r2 = await s.call_tool("submit_proposal", sub)
        assert r1.structuredContent["connects_spent"] == 6 and r2.structuredContent["connects_spent"] == 0
        tampered = await s.call_tool("create_proposal_draft", args | {"cover_letter": args["cover_letter"] + " Email me",
                                                                      "approval_token": token, "idempotency_key": "k-zzzzzzzz"})
        assert tampered.isError


@pytest.mark.anyio
async def test_input_constraints_are_enforced(fake):
    from mcp_servers.marketplace.server import mcp

    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        bad = await s.call_tool("create_proposal_draft", {"job_id": "job-1", "cover_letter": "too short", "bid_amount": -5,
                                                          "bid_type": "weekly", "idempotency_key": "x"})
        assert bad.isError
