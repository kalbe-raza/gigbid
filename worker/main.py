"""GigBid worker (E1): Arq on Redis. Scouting and every agent run happen here, never inside a web request.

Jobs
  scout_scan(user)                 saved searches -> dedup -> inbox -> analyse_job per new job
  analyse_job(user, job)           bid graph until approval/end. Idempotent: thread id = (user, job); an existing
                                   thread is never re-analysed, and Arq de-duplicates the job id while it is queued
  resume_thread(user, thread)      apply the persisted approval decision; submission is idempotent (exactly once)
  pipeline_sync(user)              marketplace events -> pipeline cards -> outcome memory; client replies -> conversation
  start_conversation(user, thread) conversation graph for a client thread
  replay_thread(user, thread, cp)  re-run a thread from a checkpoint
Cron: due saved searches every minute, pipeline sync every 2 minutes. Startup: recovery sweep for crashed runs.
Degraded mode: MCPUnavailable -> job re-queued with backoff, Redis flag set (UI banner), cleared on recovery.
Dead letter: after `max_tries` failures a run is marked dead_letter (visible in the run viewer, retryable).
"""

from __future__ import annotations

import logging
import os
import time

from arq import Retry, cron

from agent import runner
from agent.mcp_client import MCPUnavailable, ToolBox
from agent.memory import promotion_rules
from agent.memory import store as memory
from agent.nodes.scout import find_duplicate, fingerprint
from agent.settings import settings
from agent.tracing import init_tracing
from apps.api import db
from apps.api.auth.users import get_user, profile_of
from apps.api.queue import redis_settings

log = logging.getLogger("gigbid.worker")
DEGRADED_KEY = "gigbid:degraded:marketplace"
MAX_TRIES = 4


async def _degraded(ctx, on: bool) -> None:
    r = ctx["redis"]
    if on:
        await r.set(DEGRADED_KEY, str(time.time()), ex=900)
    else:
        await r.delete(DEGRADED_KEY)


def _backoff(ctx) -> int:
    return min(300, 20 * 2 ** (ctx.get("job_try", 1) - 1))


async def scout_scan(ctx, user_id: str) -> dict:
    user = await get_user(user_id)
    if not user:
        return {"error": "no user"}
    limit = settings()["scheduler"]["max_jobs_per_scan"]
    tb = ToolBox("scout", user_id, user["corpus"])
    searches = await db.fetch("SELECT * FROM saved_searches WHERE user_id=%s AND enabled", user_id)
    seen = [{"job_id": r["job_id"], "title": r["job"].get("title"), "description": r["job"].get("description", "")}
            for r in await db.fetch("SELECT job_id, job FROM inbox WHERE user_id=%s", user_id)]
    known = {s["job_id"] for s in seen}
    new = 0
    try:
        for s in searches:
            args = {"query": s["query"] or "", "domain": s["domain"], "limit": limit}
            if s["last_run"]:  # polite polling: only what was released since the previous scan
                args["posted_after"] = s["last_run"].timestamp() - 60
            res = await tb.call("marketplace", "search_jobs", args)
            for item in res.get("items", []):
                if item["job_id"] in known:
                    continue
                job = await tb.call("marketplace", "get_job", {"job_id": item["job_id"]})
                dup = find_duplicate(job, seen)
                status = "skipped" if dup else "queued"
                await db.execute("INSERT INTO inbox (user_id, job_id, fingerprint, job, status, duplicate_of, summary) "
                                 "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", user_id, job["job_id"],
                                 fingerprint(job["title"], job.get("description", "")), db.J(job), status, dup,
                                 db.J({"action": "skip", "reasons": [f"duplicate of {dup}"]} if dup else {}))
                seen.append({"job_id": job["job_id"], "title": job["title"], "description": job.get("description", "")})
                known.add(job["job_id"])
                if not dup:
                    await ctx["redis"].enqueue_job("analyse_job", user_id, job["job_id"],
                                                   _job_id=f"analyse:{user_id}:{job['job_id']}")
                    new += 1
            await db.execute("UPDATE saved_searches SET last_run=now() WHERE search_id=%s", s["search_id"])
    except MCPUnavailable as e:
        await _degraded(ctx, True)
        log.warning("scan deferred: %s", e)
        raise Retry(defer=_backoff(ctx)) from e
    await _degraded(ctx, False)
    return {"new_jobs": new}


def _summary(v: dict) -> dict:
    risk, fit, dec, price = v.get("risk") or {}, v.get("fit") or {}, v.get("decision") or {}, v.get("price") or {}
    return {"fit": fit.get("score"), "risk": risk.get("score"), "risk_level": risk.get("level"),
            "is_scam": risk.get("is_scam", False), "injection": risk.get("injection_detected", False),
            "action": dec.get("action"), "reasons": dec.get("reasons", [])[:3], "price": price.get("amount"),
            "bid_type": price.get("bid_type"), "connects": price.get("connects_cost")}


async def analyse_job(ctx, user_id: str, job_id: str, trace_id: str | None = None) -> dict:
    user = await get_user(user_id)
    row = await db.fetchone("SELECT job, duplicate_of FROM inbox WHERE user_id=%s AND job_id=%s", user_id, job_id)
    if not user or not row:
        return {"error": "missing"}
    tid = f"bid-{user_id}-{job_id}"
    await db.execute("UPDATE inbox SET status='analysing', thread_id=%s, updated_at=now() WHERE user_id=%s AND job_id=%s",
                     tid, user_id, job_id)
    s = user.get("settings") or {}
    try:
        snap = await runner.run_bid(user_id, row["job"], tid, profile_of(user), user["corpus"], s.get("autonomy", "manual"),
                                    row["duplicate_of"], trace_id)
    except MCPUnavailable as e:
        await _degraded(ctx, True)
        await db.execute("UPDATE inbox SET status='queued', updated_at=now() WHERE user_id=%s AND job_id=%s", user_id, job_id)
        # resume from the last checkpoint once the marketplace is back (run_bid is idempotent per thread)
        await ctx["redis"].enqueue_job("resume_thread", user_id, tid, _job_id=f"recover:{tid}:{int(time.time() // 60)}",
                                       _defer_by=_backoff(ctx))
        return {"deferred": str(e)}
    except Exception as e:
        if ctx.get("job_try", 1) >= MAX_TRIES:
            await db.execute("UPDATE inbox SET status='dead_letter', updated_at=now() WHERE user_id=%s AND job_id=%s",
                             user_id, job_id)
            await db.execute("UPDATE runs SET status='dead_letter', error=%s WHERE thread_id=%s", str(e)[:500], tid)
            return {"dead_letter": str(e)[:200]}
        raise Retry(defer=_backoff(ctx)) from e
    await _degraded(ctx, False)
    await _after_bid(user_id, job_id, tid, snap)
    return {"status": snap["values"].get("status"), "interrupted": bool(snap["interrupts"])}


async def _after_bid(user_id: str, job_id: str, tid: str, snap: dict) -> None:
    v = snap["values"]
    status = "awaiting_approval" if snap["interrupts"] else v.get("status", "done")
    await db.execute("UPDATE inbox SET status=%s, summary=%s, updated_at=now() WHERE user_id=%s AND job_id=%s",
                     status, db.J(_summary(v)), user_id, job_id)
    job, price = v.get("job") or {}, v.get("price") or {}
    if status == "awaiting_approval":
        await db.execute("INSERT INTO pipeline (user_id, job_id, thread_id, title, stage, category, connects, amount) "
                         "VALUES (%s,%s,%s,%s,'drafted',%s,%s,%s) ON CONFLICT (user_id, job_id) DO UPDATE SET "
                         "updated_at=now()", user_id, job_id, tid, job.get("title"), job.get("category"),
                         price.get("connects_cost") or 0, price.get("amount"))
    elif status == "submitted":
        sub = v.get("submission") or {}
        letter = (v.get("drafts") or [{}])[-1].get("cover_letter", "")
        ceiling = job.get("budget") or job.get("hourly_high")
        feats = promotion_rules.proposal_features(letter, (price.get("amount") / ceiling) if ceiling and price.get("amount") else None)
        await db.execute("INSERT INTO pipeline (user_id, job_id, thread_id, title, stage, category, connects, amount, "
                         "proposal_id, features) VALUES (%s,%s,%s,%s,'submitted',%s,%s,%s,%s,%s) ON CONFLICT (user_id, job_id) "
                         "DO UPDATE SET stage='submitted', proposal_id=EXCLUDED.proposal_id, connects=EXCLUDED.connects, "
                         "features=EXCLUDED.features, updated_at=now()", user_id, job_id, tid, job.get("title"),
                         job.get("category"), sub.get("connects_spent") or 0, price.get("amount"), sub.get("proposal_id"),
                         db.J(feats))
    elif status in ("rejected",):
        await db.execute("DELETE FROM pipeline WHERE user_id=%s AND job_id=%s AND stage='drafted'", user_id, job_id)


async def resume_thread(ctx, user_id: str, thread_id: str) -> dict:
    run = await db.fetchone("SELECT kind, ref, pending_resume FROM runs WHERE thread_id=%s AND user_id=%s", thread_id, user_id)
    if not run:
        return {"error": "no run"}
    try:
        snap = await runner.resume(run["kind"], thread_id, user_id, run["pending_resume"])
    except MCPUnavailable as e:
        await _degraded(ctx, True)
        raise Retry(defer=_backoff(ctx)) from e
    await _degraded(ctx, False)
    await db.execute("UPDATE runs SET pending_resume=NULL WHERE thread_id=%s", thread_id)
    if run["kind"] == "bid":
        await _after_bid(user_id, run["ref"], thread_id, snap)
    else:
        status = "awaiting_approval" if snap["interrupts"] else snap["values"].get("status", "done")
        await db.execute("UPDATE conversations SET status=%s, updated_at=now() WHERE user_id=%s AND graph_thread_id=%s",
                         status, user_id, thread_id)
    return {"status": snap["values"].get("status")}


STAGE = {"proposal_viewed": "viewed", "client_replied": "replied", "interview_requested": "interview",
         "offer_received": "hired", "proposal_declined": "lost"}
ORDER = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]


async def pipeline_sync(ctx, user_id: str) -> dict:
    user = await get_user(user_id)
    if not user:
        return {}
    key = f"gigbid:events:{user_id}"
    since = int(await ctx["redis"].get(key) or 0)
    try:
        res = await ToolBox("pipeline_sync", user_id, user["corpus"]).call("marketplace", "list_events", {"since": since})
    except MCPUnavailable:
        await _degraded(ctx, True)
        return {"deferred": True}
    cards = {r["proposal_id"]: r for r in await db.fetch("SELECT * FROM pipeline WHERE user_id=%s AND proposal_id IS NOT NULL",
                                                       user_id)}
    replied_now: set[str] = set()
    viewed_now: set[str] = set()
    for ev in res.get("items", []):
        since = max(since, ev["seq"])
        stage = STAGE.get(ev["type"])
        pid = ev["payload"].get("proposal_id") or ev["ref"]
        card = cards.get(pid)
        if stage and card and ORDER.index(stage) > ORDER.index(card["stage"]) or (stage == "lost" and card):
            await db.execute("UPDATE pipeline SET stage=%s, marketplace_thread_id=COALESCE(%s, marketplace_thread_id), "
                             "updated_at=now() WHERE user_id=%s AND proposal_id=%s", stage,
                             ev["ref"] if ev["type"] in ("client_replied", "interview_requested") else None, user_id, pid)
            card = dict(card) | {"stage": stage}
            cards[pid] = card
        if ev["type"] == "proposal_viewed":
            viewed_now.add(pid)
        if ev["type"] in ("client_replied", "interview_requested"):
            replied_now.add(pid)
        if ev["type"] in ("client_replied", "interview_requested", "client_message"):
            mt = ev["ref"] if ev["type"] != "client_message" else ev["payload"].get("thread_id")
            await ctx["redis"].enqueue_job("start_conversation", user_id, mt, _job_id=f"conv:{user_id}:{mt}:{ev['seq']}")
    for pid in viewed_now | replied_now:  # outcome memory: one observation per proposal, through the promotion rule
        card = cards.get(pid)
        if card:
            await memory.observe_outcome(user_id, card["category"] or "other", card["features"] or {}, pid in replied_now)
    await ctx["redis"].set(key, since)
    return {"events": len(res.get("items", []))}


async def start_conversation(ctx, user_id: str, marketplace_thread_id: str) -> dict:
    user = await get_user(user_id)
    tid = f"conv-{user_id}-{marketplace_thread_id}"
    run = await db.fetchone("SELECT status FROM runs WHERE thread_id=%s", tid)
    if run and run["status"] in ("awaiting_approval", "resuming", "running"):
        return {"skipped": "conversation already waiting for the user"}
    card = await db.fetchone("SELECT job_id FROM pipeline WHERE user_id=%s AND marketplace_thread_id=%s", user_id,
                             marketplace_thread_id)
    job = None
    if card:
        j = await db.fetchone("SELECT job FROM inbox WHERE user_id=%s AND job_id=%s", user_id, card["job_id"])
        job = (j or {}).get("job")
    await db.execute("INSERT INTO conversations (user_id, marketplace_thread_id, graph_thread_id, job_id, status) VALUES "
                     "(%s,%s,%s,%s,'running') ON CONFLICT (user_id, marketplace_thread_id) DO UPDATE SET status='running', "
                     "updated_at=now()", user_id, marketplace_thread_id, tid, (card or {}).get("job_id"))
    try:
        snap = await runner.run_conversation(user_id, marketplace_thread_id, tid, profile_of(user), user["corpus"], job)
    except MCPUnavailable as e:
        await _degraded(ctx, True)
        raise Retry(defer=_backoff(ctx)) from e
    status = "awaiting_approval" if snap["interrupts"] else snap["values"].get("status", "done")
    await db.execute("UPDATE conversations SET status=%s, updated_at=now() WHERE user_id=%s AND marketplace_thread_id=%s",
                     status, user_id, marketplace_thread_id)
    return {"status": status}


async def replay_thread(ctx, user_id: str, thread_id: str, checkpoint_id: str) -> dict:
    run = await db.fetchone("SELECT kind FROM runs WHERE thread_id=%s", thread_id)
    snap = await runner.replay(run["kind"], thread_id, checkpoint_id, user_id)
    return {"status": snap["values"].get("status")}


async def due_scans(ctx) -> None:
    rows = await db.fetch("SELECT DISTINCT user_id FROM saved_searches WHERE enabled AND "
                          "(last_run IS NULL OR last_run < now() - make_interval(secs => interval_s))")
    for r in rows:
        await ctx["redis"].enqueue_job("scout_scan", r["user_id"], _job_id=f"scan:{r['user_id']}:{int(time.time() // 60)}")


async def sync_all(ctx) -> None:
    for r in await db.fetch("SELECT DISTINCT user_id FROM pipeline WHERE stage NOT IN ('hired','lost','drafted')"):
        await ctx["redis"].enqueue_job("pipeline_sync", r["user_id"], _job_id=f"sync:{r['user_id']}:{int(time.time() // 60)}")


async def startup(ctx) -> None:
    init_tracing("worker")
    await db.init_schema()
    # Recovery sweep: approvals recorded but not applied, and runs interrupted mid-flight by a crash.
    for r in await db.fetch("SELECT user_id, thread_id FROM runs WHERE status IN ('resuming','running')"):
        await ctx["redis"].enqueue_job("resume_thread", r["user_id"], r["thread_id"], _job_id=f"recover:{r['thread_id']}:boot")


async def shutdown(ctx) -> None:
    await db.close()


class WorkerSettings:
    functions = [scout_scan, analyse_job, resume_thread, pipeline_sync, start_conversation, replay_thread]
    cron_jobs = [cron(due_scans, second=5, run_at_startup=True), cron(sync_all, minute=set(range(0, 60, 2)), second=35)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", "2"))
    job_timeout = 1200
    max_tries = MAX_TRIES
    keep_result = 3600
