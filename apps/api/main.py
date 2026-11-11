"""GigBid API (FastAPI).

  * one trace id per UI request: read from `x-trace-id` (the web client sets it) or created, returned in the response,
    passed to the worker with every enqueued job and on to every MCP call
  * logs go through a PII-masking filter (emails, phones, card numbers, secrets never reach a log line)
  * on startup: schema, reviewer accounts (REVIEWER_ACCOUNTS="email:password:persona;..."), tracing
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent.safety import mask_pii
from agent.tracing import init_tracing, new_trace_id
from apps.api import db
from apps.api.routers import analytics, approvals, auth, jobs, messages, proposals, runs
from apps.api.routers import settings as settings_router
from apps.api.streaming import router as stream_router


class PIIFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_pii(str(record.msg))
        if isinstance(record.args, tuple):  # mask only strings; %d/%s formatting must keep working
            record.args = tuple(mask_pii(a) if isinstance(a, str) else a for a in record.args)
        return True


for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "gigbid"):
    logging.getLogger(name).addFilter(PIIFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)


async def seed_reviewers() -> None:
    from apps.api.auth import token_store
    from apps.api.auth.users import create_user, profile_of

    for entry in filter(None, os.getenv("REVIEWER_ACCOUNTS", "").split(";")):
        email, password, persona = entry.split(":", 2)
        row = await db.fetchone("SELECT user_id FROM users WHERE email=%s", email.lower())
        if row:
            continue
        u = await create_user(email, password, f"Reviewer ({persona})", persona)
        settings = {"profile": profile_of(u), "weekly_connects_budget": 80, "marketplace": "gigsim", "autonomy": "manual"}
        await db.execute("UPDATE user_settings SET settings=%s WHERE user_id=%s", db.J(settings), u["user_id"])
        await db.execute("INSERT INTO saved_searches (user_id, query, domain, interval_s) VALUES (%s,%s,%s,%s)",
                         u["user_id"], "", persona, 900)
        await db.execute("UPDATE users SET onboarded=TRUE WHERE user_id=%s", u["user_id"])
        token = os.getenv("GIGSIM_DEMO_TOKEN") or os.getenv("GIGSIM_API_TOKEN")
        if token:
            await token_store.put(u["user_id"], "gigsim", {"token": token})


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_tracing("api")
    await db.init_schema()
    await seed_reviewers()
    yield
    await db.close()


app = FastAPI(title="GigBid API", version="1.0.0", lifespan=lifespan,
              description="Multi-agent freelancer copilot: discovery, assessment, grounded drafting, human approval.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def trace_ids(request: Request, call_next):
    tid = request.headers.get("x-trace-id") or new_trace_id()
    if len(tid) != 32 or any(c not in "0123456789abcdef" for c in tid):
        tid = new_trace_id()
    request.state.trace_id = tid
    response = await call_next(request)
    response.headers["x-trace-id"] = tid
    return response


for r in (auth.router, jobs.router, proposals.router, approvals.router, messages.router, runs.router, stream_router,
          analytics.router, settings_router.router):
    app.include_router(r)


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok", "service": "gigbid-api"}
