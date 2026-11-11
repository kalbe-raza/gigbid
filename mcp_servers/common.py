"""Shared plumbing for GigBid's MCP servers: bearer auth, request identity, per-user rate limiting, audit log."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent.safety import mask_pii

AUDIT_FALLBACK = Path(os.getenv("AUDIT_FALLBACK_PATH", "data/processed/audit.jsonl"))


class BearerAuth(BaseHTTPMiddleware):
    """Bearer-token auth on the MCP HTTP endpoint. Tokens come from env (comma-separated), never from code."""

    def __init__(self, app, env_var: str, open_paths: tuple[str, ...] = ("/health",)):
        super().__init__(app)
        self.tokens = {t.strip() for t in os.getenv(env_var, "").split(",") if t.strip()}
        self.open_paths = open_paths

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.open_paths:
            return await call_next(request)
        if not self.tokens:
            return JSONResponse({"error": "server has no tokens configured"}, status_code=503)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:].strip() not in self.tokens:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def identity(ctx) -> tuple[str, str | None, str | None]:
    """(user_id, trace_id, corpus) from the HTTP headers GigBid's MCP client sets; env defaults for stdio use."""
    req = None
    try:
        req = ctx.request_context.request
    except Exception:  # noqa: BLE001 - stdio transport has no HTTP request
        req = None
    h = req.headers if req is not None else {}
    meta = {}
    try:
        meta = dict(ctx.request_context.meta or {}) if ctx.request_context.meta else {}
    except Exception:  # noqa: BLE001
        meta = {}
    user = h.get("x-gigbid-user") or meta.get("user_id") or os.getenv("DEFAULT_USER_ID", "local")
    trace = h.get("x-trace-id") or meta.get("trace_id")
    corpus = h.get("x-gigbid-corpus") or meta.get("corpus")
    return user, trace, corpus


class UserRateLimiter:
    """Sliding-window limiter per user; waits (politely) instead of failing, up to `max_wait` seconds."""

    def __init__(self, per_minute: int, max_wait: float = 20):
        self.per_minute = per_minute
        self.max_wait = max_wait
        self.w: dict[str, deque] = {}
        self.lock = asyncio.Lock()

    async def acquire(self, user: str) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            async with self.lock:
                q = self.w.setdefault(user, deque())
                now = time.monotonic()
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) < self.per_minute:
                    q.append(now)
                    return
                wait = 60 - (now - q[0])
            if time.monotonic() + wait > deadline:
                raise RuntimeError(f"rate limit: more than {self.per_minute} calls/min for this user")
            await asyncio.sleep(min(wait, 2))


def _scrub(v: Any) -> Any:
    if isinstance(v, str):
        return mask_pii(v)[:2000]
    if isinstance(v, dict):
        return {k: ("[secret]" if "token" in k.lower() else _scrub(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub(x) for x in v[:50]]
    return v


async def audit(server: str, user: str, tool: str, arguments: dict, result: Any, trace_id: str | None, ok: bool) -> None:
    """Append-only audit (Postgres table with an UPDATE/DELETE-rejecting trigger; JSONL if no database)."""
    args, res = _scrub(arguments), _scrub(result if isinstance(result, dict | list | str) else str(result))
    if os.getenv("DATABASE_URL"):
        try:
            from apps.api import db

            await db.execute("INSERT INTO audit_log (server, user_id, tool, arguments, result, trace_id, ok) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s)", server, user, tool, db.J(args), db.J(res), trace_id, ok)
            return
        except Exception:  # noqa: BLE001 - never lose an audit line: fall back to the file
            pass
    AUDIT_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_FALLBACK.open("a") as f:
        f.write(json.dumps({"at": time.time(), "server": server, "user": user, "tool": tool, "arguments": args,
                            "result": res, "trace_id": trace_id, "ok": ok}, default=str) + "\n")
