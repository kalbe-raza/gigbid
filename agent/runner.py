"""Runs the graphs with durable state (C6): Postgres checkpointer + store, per-thread ledgers, live events.

    await run_bid(...)            new job -> runs until the approval interrupt or the end
    await resume(thread, value)   approve / edit / reject from the UI -> continues from the checkpoint
    await history(thread)         checkpoint history (replay any job from any node)
    await replay(thread, cp_id)   re-execute from a past checkpoint

Every node's progress is written to `run_events` (Postgres), which the API streams to the browser over SSE, so a
page refresh or an API restart never loses progress or a pending approval: both live in Postgres, not in memory.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

from langgraph.types import Command

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.llm import Ledger, current_ledger
from agent.memory.store import get_store
from agent.safety import mask_pii
from agent.tracing import trace_root, trace_url

_saver = None
_graphs: dict[str, Any] = {}


async def checkpointer():
    global _saver
    if _saver is None:
        if os.getenv("DATABASE_URL"):
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            from apps.api.db import database_url

            pool = AsyncConnectionPool(database_url(), max_size=6, open=False,
                                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
            await pool.open()
            _saver = AsyncPostgresSaver(pool)
            await _saver.setup()
        else:
            from langgraph.checkpoint.memory import InMemorySaver

            _saver = InMemorySaver()
    return _saver


async def graph(kind: str):
    if kind not in _graphs:
        cp, store = await checkpointer(), await get_store()
        _graphs[kind] = build_bid_graph(cp, store) if kind == "bid" else build_conversation_graph(cp, store)
    return _graphs[kind]


class EventSink:
    """Writes progress events to Postgres (run_events) for SSE; no-op without a database."""

    def __init__(self, thread_id: str, user_id: str):
        self.thread_id, self.user_id = thread_id, user_id

    async def __call__(self, node: str, kind: str, payload: dict) -> None:
        if not os.getenv("DATABASE_URL"):
            return
        from apps.api import db

        await db.execute("INSERT INTO run_events (thread_id, user_id, node, kind, payload) VALUES (%s,%s,%s,%s,%s)",
                         self.thread_id, self.user_id, node, kind, db.J(_mask(payload)))


def _mask(v):
    if isinstance(v, str):
        return mask_pii(v)
    if isinstance(v, dict):
        return {k: _mask(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x) for x in v]
    return v


async def _set_run(thread_id: str, user_id: str, kind: str, ref: str, status: str, trace_id: str | None,
                   usage: dict | None = None, error: str | None = None) -> None:
    if not os.getenv("DATABASE_URL"):
        return
    from apps.api import db

    await db.execute(
        "INSERT INTO runs (thread_id, user_id, kind, ref, status, trace_id, usage, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (thread_id) DO UPDATE SET status=EXCLUDED.status, trace_id=COALESCE(EXCLUDED.trace_id, runs.trace_id), "
        "usage=runs.usage || EXCLUDED.usage, error=EXCLUDED.error, updated_at=now()",
        thread_id, user_id, kind, ref, status, trace_id, db.J(usage or {}), error)


@asynccontextmanager
async def _ledger():
    led = Ledger()
    tok = current_ledger.set(led)
    try:
        yield led
    finally:
        current_ledger.reset(tok)


async def _drive(g, inp, cfg, sink: EventSink) -> None:
    async for mode, data in g.astream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom":
            await sink(data.get("node", "?"), data.get("kind", "event"), {k: v for k, v in data.items()
                                                                            if k not in ("node", "kind")})
        elif mode == "updates" and "__interrupt__" in data:
            await sink("human_approval", "interrupt", {"waiting": True})


async def snapshot(kind: str, thread_id: str) -> dict:
    g = await graph(kind)
    s = await g.aget_state({"configurable": {"thread_id": thread_id}})
    interrupts = [i.value for t in (s.tasks or []) for i in (t.interrupts or [])]
    return {"values": s.values or {}, "next": list(s.next or []), "interrupts": interrupts,
            "checkpoint_id": (s.config or {}).get("configurable", {}).get("checkpoint_id")}


def _usage_summary(values: dict, led: Ledger) -> dict:
    return {"tokens": led.tokens, "cost_usd": led.cost, "nodes": led.by_node()}


async def run_bid(user_id: str, job: dict, thread_id: str, profile: dict, corpus: str, autonomy: str = "manual",
                  duplicate_of: str | None = None, trace_id: str | None = None) -> dict:
    g = await graph("bid")
    cfg = {"configurable": {"thread_id": thread_id, "user_id": user_id, "corpus": corpus}}
    existing = await g.aget_state(cfg)
    if existing.values:  # idempotent: a worker restart never re-analyses a job that already has a thread
        return await snapshot("bid", thread_id)
    sink = EventSink(thread_id, user_id)
    t0 = time.perf_counter()
    async with _ledger() as led:
        with trace_root("bid_graph", trace_id, thread_id=thread_id, user_id=user_id, job_id=job.get("job_id")) as tid:
            await _set_run(thread_id, user_id, "bid", job.get("job_id", ""), "running", tid)
            state = {"thread_id": thread_id, "user_id": user_id, "corpus": corpus, "trace_id": tid, "profile": profile,
                     "job": job, "autonomy": autonomy, "duplicate_of": duplicate_of, "steps": 0}
            try:
                await _drive(g, state, cfg | {"configurable": cfg["configurable"] | {"trace_id": tid}}, sink)
            except Exception as e:
                await _set_run(thread_id, user_id, "bid", job.get("job_id", ""), "error", tid, error=mask_pii(str(e))[:500])
                raise
            snap = await snapshot("bid", thread_id)
            status = "awaiting_approval" if snap["interrupts"] else snap["values"].get("status", "done")
            usage = _usage_summary(snap["values"], led) | {"latency_s": round(time.perf_counter() - t0, 2)}
            await _set_run(thread_id, user_id, "bid", job.get("job_id", ""), status, tid, usage)
            snap["usage"] = usage
            snap["trace_url"] = trace_url(tid)
            return snap


async def resume(kind: str, thread_id: str, user_id: str, value: dict | None) -> dict:
    """Resume after the human decided. Safe to call twice: if the interrupt was already consumed it just continues
    (and submission is idempotent), if the thread already finished it returns the final state."""
    g = await graph(kind)
    cfg = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    snap = await snapshot(kind, thread_id)
    if not snap["next"]:
        return snap
    sink = EventSink(thread_id, user_id)
    tid = snap["values"].get("trace_id")
    async with _ledger() as led:
        with trace_root(f"{kind}_graph.resume", tid, thread_id=thread_id, user_id=user_id):
            inp = Command(resume=value) if snap["interrupts"] and value is not None else None
            await _drive(g, inp, cfg, sink)
        out = await snapshot(kind, thread_id)
        status = "awaiting_approval" if out["interrupts"] else out["values"].get("status", "done")
        await _set_run(thread_id, user_id, kind, snap["values"].get("job", {}).get("job_id", ""), status, tid,
                       {"resume_tokens": led.tokens, "resume_cost_usd": led.cost})
        return out


async def run_conversation(user_id: str, marketplace_thread_id: str, thread_id: str, profile: dict, corpus: str,
                           job: dict | None = None) -> dict:
    g = await graph("conversation")
    cfg = {"configurable": {"thread_id": thread_id, "user_id": user_id, "corpus": corpus}}
    sink = EventSink(thread_id, user_id)
    async with _ledger() as led:
        with trace_root("conversation_graph", None, thread_id=thread_id, user_id=user_id) as tid:
            await _set_run(thread_id, user_id, "conversation", marketplace_thread_id, "running", tid)
            state = {"thread_id": thread_id, "user_id": user_id, "corpus": corpus, "trace_id": tid, "profile": profile,
                     "marketplace_thread_id": marketplace_thread_id, "job": job or {}, "steps": 0}
            await _drive(g, state, cfg, sink)
            snap = await snapshot("conversation", thread_id)
            status = "awaiting_approval" if snap["interrupts"] else snap["values"].get("status", "done")
            await _set_run(thread_id, user_id, "conversation", marketplace_thread_id, status, tid,
                           {"tokens": led.tokens, "cost_usd": led.cost})
            return snap


async def history(kind: str, thread_id: str, limit: int = 50) -> list[dict]:
    g = await graph(kind)
    out = []
    async for s in g.aget_state_history({"configurable": {"thread_id": thread_id}}, limit=limit):
        meta = s.metadata or {}
        out.append({"checkpoint_id": s.config["configurable"]["checkpoint_id"], "next": list(s.next or []),
                    "step": meta.get("step"), "writes": list((meta.get("writes") or {}).keys()),
                    "status": (s.values or {}).get("status"), "created_at": s.created_at})
    return out


async def replay(kind: str, thread_id: str, checkpoint_id: str, user_id: str) -> dict:
    """Re-execute a thread from a past checkpoint (forks history; the original stays in the checkpoint log)."""
    g = await graph(kind)
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id, "user_id": user_id}}
    sink = EventSink(thread_id, user_id)
    async with _ledger():
        await _drive(g, None, cfg, sink)
    return await snapshot(kind, thread_id)
