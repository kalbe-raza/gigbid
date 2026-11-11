"""Bid / watch / skip decision. Rules from config (thresholds), so every skip has a stated reason."""

from __future__ import annotations

from agent.handoffs import BidDecision
from agent.nodes.common import NodeContext, gigbid_node
from agent.settings import settings


def decide(job: dict, risk: dict, fit: dict, profile: dict, autonomy: str = "manual",
           duplicate_of: str | None = None) -> BidDecision:
    th = settings().thresholds
    reasons: list[str] = []
    if duplicate_of:
        return BidDecision(action="skip", reasons=[f"duplicate of {duplicate_of}"], confidence=0.95, auto=True)
    if risk.get("is_scam"):
        codes = sorted({f["code"] for f in risk.get("flags", []) if f["severity"] == "high"})
        return BidDecision(action="skip", reasons=["scam signals: " + ", ".join(codes or ["llm judgement"])],
                           confidence=0.9, auto=autonomy == "auto_skip_low_fit")
    if risk.get("score", 0) >= th["max_risk"]:
        return BidDecision(action="skip", reasons=[f"risk {risk['score']:.2f} above limit {th['max_risk']}"],
                           confidence=0.8)
    floor_h, min_project = profile.get("hourly_floor", 0), profile.get("min_project", 0)
    if job.get("is_hourly") and job.get("hourly_high") and job["hourly_high"] < floor_h:
        reasons.append(f"hourly ceiling ${job['hourly_high']:.0f} is below your floor ${floor_h}")
    if not job.get("is_hourly") and job.get("budget") is not None and job["budget"] < min_project:
        reasons.append(f"budget ${job['budget']:.0f} is below your minimum project ${min_project}")
    f = fit.get("score", 0.0)
    if reasons:
        return BidDecision(action="skip", reasons=reasons + [f"fit {f:.2f}"], confidence=0.85)
    if f >= th["bid_min_fit"]:
        why = [f"fit {f:.2f} >= {th['bid_min_fit']}"] + fit.get("reasons", [])[:3]
        if risk.get("injection_detected"):
            why.append("post contains instructions aimed at AI tools; they are ignored")
        return BidDecision(action="bid", reasons=why, confidence=min(0.95, 0.5 + f / 2))
    if f >= th["watch_min_fit"]:
        return BidDecision(action="watch", reasons=[f"fit {f:.2f} is borderline"] + fit.get("reasons", [])[:2],
                           confidence=0.5)
    auto = autonomy == "auto_skip_low_fit" and f < settings()["autonomy"]["auto_skip_below_fit"]
    return BidDecision(action="skip", reasons=[f"fit {f:.2f} below {th['watch_min_fit']}"] + fit.get("reasons", [])[1:3],
                       confidence=0.75, auto=auto)


@gigbid_node("decide")
async def decision_node(state: dict, ctx: NodeContext) -> dict:
    d = decide(state["job"], state.get("risk", {}), state.get("fit", {}), state.get("profile") or {},
               state.get("autonomy", "manual"), state.get("duplicate_of"))
    status = {"bid": "pricing", "watch": "watch", "skip": "skipped"}[d.action]
    return {"decision": d.model_dump(), "status": status, "_summary": f"{d.action}: {d.reasons[0]}"}
