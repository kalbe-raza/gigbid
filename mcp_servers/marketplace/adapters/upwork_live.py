"""Live adapter: the official Upwork MCP server (https://mcp.upwork.com/mcp) over Streamable HTTP through
`MultiServerMCPClient`, authenticated with the user's OAuth 2.1 access token (obtained by
apps/api/auth/oauth_upwork.py and stored encrypted per user).

Rules enforced here, not just documented:
  * every write is a DRAFT. `submit_proposal` and `send_message` never finalise anything on Upwork; the user
    must confirm on Upwork itself, so they return status "awaiting_platform_confirmation";
  * read tools are only called by the scheduled scout at the conservative interval in config (RUNBOOK);
  * no browser automation, no cookies, no scraping: the only network peer is the official MCP endpoint.

Upwork's tool names are resolved at runtime from `list_tools()` using the candidate names below, so a rename
on Upwork's side is a config change. Verify with `make upwork-tools` after connecting an account.
NOTE: developed and tested against tests/integration/mock_upwork_mcp.py; it has not been exercised against a
real Upwork account (none was available) - see docs/TOOLS.md.
"""

from __future__ import annotations

import json
from typing import Any

from agent.marketplace_port import MarketplaceError, MarketplaceUnavailable

TOOL_CANDIDATES: dict[str, list[str]] = {
    "search_jobs": ["search_jobs", "job_search", "find_jobs", "searchJobs"],
    "get_job": ["get_job", "get_job_details", "job_details", "getJob"],
    "get_client_history": ["get_client", "get_client_history", "client_details"],
    "get_connects_balance": ["get_connects_balance", "connects_balance", "get_connects"],
    "list_proposals": ["list_proposals", "get_proposals", "my_proposals"],
    "list_threads": ["list_messages", "list_rooms", "get_messages", "list_threads"],
    "create_proposal_draft": ["create_proposal_draft", "draft_proposal", "create_proposal"],
    "send_message": ["draft_message", "create_message_draft", "send_message"],
}


class UpworkLiveAdapter:
    name = "upwork_live"

    def __init__(self, mcp_url: str, access_token: str):
        self.mcp_url = mcp_url
        self.access_token = access_token
        self._tools: dict[str, str] | None = None

    def _client(self):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        return MultiServerMCPClient({"upwork": {"transport": "streamable_http", "url": self.mcp_url,
                                                "headers": {"Authorization": f"Bearer {self.access_token}"}}})

    async def _resolve(self, session) -> dict[str, str]:
        if self._tools is None:
            available = {t.name for t in (await session.list_tools()).tools}
            self._tools = {}
            for op, names in TOOL_CANDIDATES.items():
                for n in names:
                    if n in available:
                        self._tools[op] = n
                        break
        return self._tools

    async def _call(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client().session("upwork") as s:
                tools = await self._resolve(s)
                if op not in tools:
                    raise MarketplaceError(f"Upwork MCP exposes no tool for {op}")
                res = await s.call_tool(tools[op], args)
        except MarketplaceError:
            raise
        except Exception as e:  # noqa: BLE001 - network/auth failures surface as degraded mode
            raise MarketplaceUnavailable(f"Upwork MCP unavailable: {type(e).__name__}") from e
        if res.isError:
            raise MarketplaceError(" ".join(getattr(c, "text", "") for c in res.content)[:300])
        if res.structuredContent:
            return dict(res.structuredContent)
        text = " ".join(getattr(c, "text", "") for c in res.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    # ------------------------------------------------------------------ read
    async def search_jobs(self, query: str = "", domain: str | None = None, limit: int = 20,
                          posted_after: float | None = None) -> dict[str, Any]:
        out = await self._call("search_jobs", {"query": query or (domain or ""), "limit": limit})
        items = out.get("items") or out.get("jobs") or []
        return {"total": len(items), "items": items}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._call("get_job", {"job_id": job_id})

    async def get_client_history(self, client_id: str) -> dict[str, Any]:
        return await self._call("get_client_history", {"client_id": client_id})

    async def get_connects_balance(self) -> dict[str, Any]:
        return await self._call("get_connects_balance", {})

    async def get_market_stats(self, domain: str | None = None) -> dict[str, Any]:
        # Upwork does not publish market statistics through MCP; pricing falls back to the bundled
        # historical statistics (data/market/market_stats.json), which the Pricing node already uses.
        from pathlib import Path

        s = json.loads((Path(__file__).resolve().parents[3] / "data/market/market_stats.json").read_text())
        return {"price": s["price"].get(domain) if domain else s["price"],
                "hourly_contract_rates": s["contract_hourly_rates"].get(domain) if domain else s["contract_hourly_rates"],
                "sources": s["sources"], "note": "bundled historical statistics (live mode)"}

    async def get_policy(self) -> dict[str, Any]:
        return {"version": "upwork-live", "connects": {"surcharge_rules": []}, "proposal": {}}

    async def list_proposals(self) -> dict[str, Any]:
        return await self._call("list_proposals", {})

    async def list_threads(self) -> dict[str, Any]:
        return await self._call("list_threads", {})

    async def list_events(self, since: int = 0) -> dict[str, Any]:
        return {"items": []}  # live mode derives pipeline state from list_proposals on the scout schedule

    # ------------------------------------------------------------------ write (drafts only)
    async def create_proposal_draft(self, job_id: str, cover_letter: str, screening_answers: list[str],
                                    bid_amount: float, bid_type: str, idempotency_key: str) -> dict[str, Any]:
        out = await self._call("create_proposal_draft", {
            "job_id": job_id, "cover_letter": cover_letter, "answers": screening_answers, "amount": bid_amount,
            "rate_type": bid_type, "client_reference": idempotency_key})
        return {"proposal_id": out.get("proposal_id") or out.get("draft_id") or out.get("id"), "status": "draft", **out}

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]:
        # Upwork requires the freelancer to confirm the draft on Upwork. We never finalise it here.
        return {"proposal_id": proposal_id, "status": "awaiting_platform_confirmation", "connects_spent": 0,
                "note": "Open Upwork to review and submit this draft."}

    async def send_message(self, thread_id: str, text: str, idempotency_key: str) -> dict[str, Any]:
        out = await self._call("send_message", {"room_id": thread_id, "text": text, "client_reference": idempotency_key})
        return {"thread_id": thread_id, "status": "awaiting_platform_confirmation", **out}
