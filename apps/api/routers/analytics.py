"""Analytics: reply and hire rate by category, Connects spent vs outcomes, cost/latency per proposal, top rejection
reasons, and the pipeline board (cards updated from marketplace events by the worker)."""

from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import APIRouter

from apps.api import db
from apps.api.auth.users import CurrentUser
from apps.api.schemas.models import Analytics, PipelineCard

router = APIRouter(prefix="/api", tags=["analytics"])
REPLIED = {"replied", "interview", "hired"}


@router.get("/pipeline", response_model=list[PipelineCard])
async def pipeline(user=CurrentUser):
    rows = await db.fetch("SELECT * FROM pipeline WHERE user_id=%s ORDER BY updated_at DESC", user["user_id"])
    return [PipelineCard(job_id=r["job_id"], title=r["title"], stage=r["stage"], category=r["category"],
                         connects=r["connects"] or 0, amount=float(r["amount"]) if r["amount"] is not None else None,
                         thread_id=r["thread_id"], updated_at=r["updated_at"].isoformat()) for r in rows]


@router.get("/analytics", response_model=Analytics)
async def analytics(user=CurrentUser):
    uid = user["user_id"]
    cards = await db.fetch("SELECT * FROM pipeline WHERE user_id=%s", uid)
    by_cat: dict[str, Counter] = defaultdict(Counter)
    funnel = Counter()
    for c in cards:
        cat = c["category"] or "other"
        if c["stage"] != "drafted":
            by_cat[cat]["sent"] += 1
            by_cat[cat]["connects"] += c["connects"] or 0
            if c["stage"] in REPLIED:
                by_cat[cat]["replied"] += 1
            if c["stage"] == "hired":
                by_cat[cat]["hired"] += 1
        funnel[c["stage"]] += 1
    rates = [{"category": k, "sent": v["sent"], "replied": v["replied"], "hired": v["hired"],
              "reply_rate": round(v["replied"] / v["sent"], 3) if v["sent"] else None,
              "hire_rate": round(v["hired"] / v["sent"], 3) if v["sent"] else None, "connects": v["connects"]}
             for k, v in sorted(by_cat.items())]
    spent = sum(v["connects"] for v in by_cat.values())
    replies = sum(v["replied"] for v in by_cat.values())
    runs = await db.fetch("SELECT kind, status, usage FROM runs WHERE user_id=%s", uid)
    analysed = [r["usage"] for r in runs if r["kind"] == "bid" and r["usage"]]
    proposals = [u for r, u in ((r, r["usage"]) for r in runs if r["kind"] == "bid")
                 if u and (r["status"] in ("awaiting_approval", "submitted", "rejected"))]

    def stats(xs: list[dict], key: str) -> dict:
        vals = sorted(float(x.get(key) or 0) for x in xs)
        if not vals:
            return {"n": 0}
        p = lambda q: vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]  # noqa: E731
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 5), "p50": round(p(0.5), 5), "p95": round(p(0.95), 5)}

    rej = await db.fetch("SELECT reason FROM approvals_log WHERE user_id=%s AND decision='reject'", uid)
    reasons = Counter((r["reason"] or "no reason").strip().lower()[:80] for r in rej)
    return Analytics(
        reply_rate_by_category=rates,
        connects={"spent": spent, "replies": replies, "connects_per_reply": round(spent / replies, 2) if replies else None},
        cost_latency={"per_job_analysed": {"cost_usd": stats(analysed, "cost_usd"), "latency_s": stats(analysed, "latency_s")},
                      "per_proposal": {"cost_usd": stats(proposals, "cost_usd"), "latency_s": stats(proposals, "latency_s")}},
        rejection_reasons=[{"reason": k, "count": v} for k, v in reasons.most_common(8)],
        funnel={k: funnel.get(k, 0) for k in ("drafted", "submitted", "viewed", "replied", "interview", "hired", "lost")},
    )
