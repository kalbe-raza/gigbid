"""GigSim adapter: REST calls with timeouts, bounded retries with backoff, Retry-After handling and a client-side
per-user rate limiter that stays under GigSim's limit."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import httpx

from agent.marketplace_port import MarketplaceError, MarketplaceRateLimited, MarketplaceUnavailable

_WINDOWS: dict[str, deque] = {}
_LOCK = asyncio.Lock()


async def _throttle(token: str, per_minute: int) -> None:
    while True:
        async with _LOCK:
            w = _WINDOWS.setdefault(token, deque())
            now = time.monotonic()
            while w and now - w[0] > 60:
                w.popleft()
            if len(w) < per_minute:
                w.append(now)
                return
            wait = 60 - (now - w[0]) + 0.05
        await asyncio.sleep(min(wait, 5))


class GigSimAdapter:
    name = "gigsim"

    def __init__(self, base_url: str, token: str, per_minute: int = 40, attempts: int = 3, backoff: float = 1.0,
                 timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.per_minute = per_minute
        self.attempts = attempts
        self.backoff = backoff
        self.timeout = timeout

    async def _req(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None,
                   idem: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idem:
            headers["Idempotency-Key"] = idem
        last: Exception | None = None
        for attempt in range(self.attempts):
            await _throttle(self.token, self.per_minute)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    r = await c.request(method, self.base_url + path, params=params, json=json, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = MarketplaceUnavailable(f"GigSim unreachable: {type(e).__name__}")
                await asyncio.sleep(self.backoff * 2 ** attempt)
                continue
            if r.status_code == 429:
                ra = float(r.headers.get("Retry-After", "5"))
                last = MarketplaceRateLimited(ra)
                if ra > 20:
                    raise last
                await asyncio.sleep(ra)
                continue
            if r.status_code >= 500:
                last = MarketplaceUnavailable(f"GigSim HTTP {r.status_code}")
                await asyncio.sleep(self.backoff * 2 ** attempt)
                continue
            if r.status_code >= 400:
                detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
                raise MarketplaceError(f"GigSim HTTP {r.status_code}: {detail}")
            return r.json()
        raise last or MarketplaceUnavailable("GigSim failed")

    # ------------------------------------------------------------------ read
    async def search_jobs(self, query: str = "", domain: str | None = None, limit: int = 20,
                          posted_after: float | None = None) -> dict[str, Any]:
        params = {"q": query, "limit": limit}
        if domain:
            params["domain"] = domain
        if posted_after:
            params["posted_after"] = posted_after
        return await self._req("GET", "/v1/jobs/search", params=params)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/v1/jobs/{job_id}")

    async def get_client_history(self, client_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/v1/clients/{client_id}")

    async def get_connects_balance(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/connects")

    async def get_market_stats(self, domain: str | None = None) -> dict[str, Any]:
        return await self._req("GET", "/v1/market/stats", params={"domain": domain} if domain else None)

    async def get_policy(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/policy")

    async def list_proposals(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/proposals")

    async def list_threads(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/threads")

    async def list_events(self, since: int = 0) -> dict[str, Any]:
        return await self._req("GET", "/v1/events", params={"since": since})

    # ------------------------------------------------------------------ write
    async def create_proposal_draft(self, job_id: str, cover_letter: str, screening_answers: list[str],
                                    bid_amount: float, bid_type: str, idempotency_key: str) -> dict[str, Any]:
        return await self._req("POST", "/v1/proposals", idem=idempotency_key, json={
            "job_id": job_id, "cover_letter": cover_letter, "screening_answers": screening_answers,
            "bid_amount": bid_amount, "bid_type": bid_type})

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]:
        return await self._req("POST", f"/v1/proposals/{proposal_id}/submit", idem=idempotency_key)

    async def send_message(self, thread_id: str, text: str, idempotency_key: str) -> dict[str, Any]:
        return await self._req("POST", f"/v1/threads/{thread_id}/messages", idem=idempotency_key, json={"text": text})
