"""Job inbox (ranked by fit, with risk badges, action, price, Connects) and job detail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent import runner
from agent.tracing import trace_url
from apps.api import db
from apps.api.auth.users import CurrentUser
from apps.api.queue import enqueue
from apps.api.schemas.models import InboxItem, JobDetail

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[InboxItem])
async def inbox(user=CurrentUser, status: str | None = None, min_fit: float | None = None, hide_skipped: bool = False):
    rows = await db.fetch("SELECT job_id, job, status, summary, duplicate_of, thread_id, updated_at FROM inbox "
                          "WHERE user_id=%s ORDER BY updated_at DESC LIMIT 300", user["user_id"])
    out = []
    for r in rows:
        s = r["summary"] or {}
        item = InboxItem(job_id=r["job_id"], title=r["job"].get("title", ""), category=r["job"].get("category"),
                         status=r["status"], fit=s.get("fit"), risk=s.get("risk"), risk_level=s.get("risk_level"),
                         is_scam=bool(s.get("is_scam")), injection=bool(s.get("injection")), action=s.get("action"),
                         price=s.get("price"), bid_type=s.get("bid_type"),
                         connects=s.get("connects") or r["job"].get("connects_cost"), duplicate_of=r["duplicate_of"],
                         thread_id=r["thread_id"], updated_at=r["updated_at"].isoformat())
        if status and item.status != status:
            continue
        if min_fit is not None and (item.fit or 0) < min_fit:
            continue
        if hide_skipped and item.status in ("skipped", "dismissed"):
            continue
        out.append(item)
    rank = {"awaiting_approval": 0, "analysing": 1, "queued": 2, "watch": 3, "submitted": 4}
    out.sort(key=lambda i: (rank.get(i.status, 5), -(i.fit or 0)))
    return out


@router.get("/{job_id}", response_model=JobDetail)
async def detail(job_id: str, user=CurrentUser):
    r = await db.fetchone("SELECT job, status, thread_id FROM inbox WHERE user_id=%s AND job_id=%s", user["user_id"], job_id)
    if not r:
        raise HTTPException(404, "job not in your inbox")
    out = JobDetail(job=r["job"], status=r["status"], thread_id=r["thread_id"])
    if r["thread_id"]:
        snap = await runner.snapshot("bid", r["thread_id"])
        v = snap["values"]
        out.job = v.get("job") or r["job"]
        out.job_spec, out.risk, out.fit = v.get("job_spec"), v.get("risk"), v.get("fit")
        out.decision, out.price, out.errors = v.get("decision"), v.get("price"), v.get("errors") or []
        out.trace_url = trace_url(v.get("trace_id"))
    return out


@router.post("/{job_id}/skip", response_model=InboxItem)
async def skip(job_id: str, user=CurrentUser):
    await db.execute("UPDATE inbox SET status='dismissed', updated_at=now() WHERE user_id=%s AND job_id=%s",
                     user["user_id"], job_id)
    items = [i for i in await inbox(user) if i.job_id == job_id]
    if not items:
        raise HTTPException(404, "job not in your inbox")
    return items[0]


@router.post("/{job_id}/analyse")
async def analyse(job_id: str, user=CurrentUser) -> dict:
    r = await db.fetchone("SELECT status FROM inbox WHERE user_id=%s AND job_id=%s", user["user_id"], job_id)
    if not r:
        raise HTTPException(404, "job not in your inbox")
    queued = await enqueue("analyse_job", user["user_id"], job_id, job_id=f"analyse:{user['user_id']}:{job_id}")
    return {"queued": queued}


@router.post("/scan")
async def scan_now(user=CurrentUser) -> dict:
    import time

    queued = await enqueue("scout_scan", user["user_id"], job_id=f"scan:{user['user_id']}:{int(time.time() // 60)}")
    return {"queued": queued}
