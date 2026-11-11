"""R1: the single marketplace interface. Two adapters implement it:

    mcp_servers/marketplace/adapters/gigsim.py       GigSim REST simulator (development + grading)
    mcp_servers/marketplace/adapters/upwork_live.py  official Upwork MCP server (OAuth 2.1, drafts only)

`marketplace.mode` in agent/config.yaml picks the adapter. The Marketplace MCP server is the only caller of
this port; graph nodes call the MCP server's tools, so the graph cannot tell which marketplace is behind it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class MarketplaceError(RuntimeError):
    """Non-retryable error from the marketplace (bad request, not found, policy rejection)."""


class MarketplaceUnavailable(MarketplaceError):
    """Marketplace (or our marketplace MCP server) is down / failing. Work is queued, UI shows degraded mode."""


class MarketplaceRateLimited(MarketplaceUnavailable):
    def __init__(self, retry_after: float, msg: str = "rate limited"):
        super().__init__(msg)
        self.retry_after = retry_after


@runtime_checkable
class MarketplacePort(Protocol):
    name: str

    # read
    async def search_jobs(self, query: str = "", domain: str | None = None, limit: int = 20,
                          posted_after: float | None = None) -> dict[str, Any]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self) -> dict[str, Any]: ...
    async def get_market_stats(self, domain: str | None = None) -> dict[str, Any]: ...
    async def get_policy(self) -> dict[str, Any]: ...
    async def list_proposals(self) -> dict[str, Any]: ...
    async def list_threads(self) -> dict[str, Any]: ...
    async def list_events(self, since: int = 0) -> dict[str, Any]: ...

    # write (each takes an idempotency key; callers derive it from the approved artefact)
    async def create_proposal_draft(self, job_id: str, cover_letter: str, screening_answers: list[str],
                                    bid_amount: float, bid_type: str, idempotency_key: str) -> dict[str, Any]: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def send_message(self, thread_id: str, text: str, idempotency_key: str) -> dict[str, Any]: ...


def make_adapter(mode: str, credentials: dict[str, Any]) -> MarketplacePort:
    """Factory used by the Marketplace MCP server. `credentials` come from the encrypted per-user token store."""
    if mode == "gigsim":
        from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter

        return GigSimAdapter(base_url=credentials["base_url"], token=credentials["token"])
    if mode == "upwork_live":
        from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter

        return UpworkLiveAdapter(mcp_url=credentials["mcp_url"], access_token=credentials["access_token"])
    raise ValueError(f"unknown marketplace mode {mode!r}")
