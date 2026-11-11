"""Risk Assessor: client quality from marketplace history + scam/free-work/off-platform/injection flags.

Layered, cheapest first:
  1. deterministic patterns over the post (scam phrases, off-platform asks, injection phrasing)
  2. Llama Prompt Guard 2 injection probability (tiny classifier model)
  3. an LLM judgement, only when 1-2 are ambiguous (config: risk_llm) - saves tokens on clear cases
"""

from __future__ import annotations

import re

from agent.handoffs import ClientQuality, RiskFlag, RiskLLMOut, RiskReport
from agent.llm import LLMError, chat, classify_injection, load_prompt
from agent.nodes.common import NodeContext, gigbid_node
from agent.safety import injection_signals, wrap_untrusted
from agent.settings import settings

PATTERNS: list[tuple[str, str, str]] = [
    ("unpaid_work", "high", r"\b(unpaid|for free|free (test|trial|sample)|test (is|task is) unpaid|pay (you )?after (i|we) "
                            r"(check|test|see)|complete (the )?(full|entire|complete) .{0,40}first)\b"),
    ("unpaid_work", "high", r"\b(revenue share|equity only|paid from future revenue|no budget yet)\b"),
    ("off_platform_contact", "high", r"\b(telegram|whats ?app|signal app|skype|wechat|contact me (at|on)|email me at|"
                                     r"message me on)\b"),
    ("off_platform_payment", "high", r"\b(pay(ment)? (by|in|via) (crypto|bitcoin|usdt|paypal|wire)|crypto every week|"
                                     r"outside (of )?(upwork|the platform))\b"),
    ("upfront_fee", "high", r"\b(training fee|registration fee|fee .{0,20}(required|to unlock)|buy your (work )?laptop)\b"),
    ("check_fraud", "high", r"\b(send you a che(que|ck)|deposit (it|the cheque|the check)|remaining balance to)\b"),
    ("personal_data_request", "high", r"\b(bank details|bank account|your id\b|passport|ssn|social security|password)\b"),
    ("vague_scope", "low", r"\b(simple task|easy money|no experience needed|no interview)\b"),
]
SEVERITY = {"low": 0.15, "medium": 0.4, "high": 0.75}


def pattern_flags(text: str) -> list[RiskFlag]:
    flags: dict[str, RiskFlag] = {}
    for code, sev, pat in PATTERNS:
        m = re.search(pat, text, re.I)
        if m and code not in flags:
            flags[code] = RiskFlag(code=code, severity=sev, quote=m.group(0)[:80])
    return list(flags.values())


def client_quality(c: dict) -> ClientQuality:
    notes, score = [], 0.5
    if c.get("payment_verified"):
        score += 0.2
    else:
        score -= 0.25
        notes.append("payment method not verified")
    spent = c.get("total_spent") or 0
    if spent >= 10000:
        score += 0.2
    elif spent >= 1000:
        score += 0.1
    elif spent == 0:
        score -= 0.1
        notes.append("has never spent on the platform")
    rating = c.get("rating")
    if rating is not None and rating < 4.0 and (c.get("feedback_count") or 0) >= 3:
        score -= 0.2
        notes.append(f"low rating from freelancers ({rating})")
    if (c.get("hires") or 0) == 0:
        notes.append("no previous hires")
    return ClientQuality(score=max(0.0, min(1.0, score)), payment_verified=c.get("payment_verified"),
                         total_spent=spent, rating=rating, hires=c.get("hires"), notes=notes)


def combine(flags: list[RiskFlag], cq: ClientQuality, injection: bool, llm_scam: bool | None) -> tuple[float, bool]:
    base = max([SEVERITY[f.severity] for f in flags], default=0.05)
    highs = sum(f.severity == "high" for f in flags if f.code != "prompt_injection")
    score = base + 0.1 * max(0, highs - 1) + (0.5 - cq.score) * 0.3
    if injection:
        score = max(score, 0.45)
    if llm_scam:
        score = max(score, 0.85)
    is_scam = bool(llm_scam) or highs >= 1 and any(f.code in {"upfront_fee", "check_fraud", "off_platform_payment",
                                                              "personal_data_request", "unpaid_work",
                                                              "off_platform_contact"} for f in flags)
    return round(max(0.0, min(1.0, score)), 3), is_scam


async def assess(job: dict, client: dict, use_llm: str = "ambiguous") -> RiskReport:
    text = f"{job.get('title', '')}\n{job.get('description', '')}"
    flags = pattern_flags(text)
    cq = client_quality(client)
    if cq.payment_verified is False:
        flags.append(RiskFlag(code="unverified_payment", severity="medium", quote=""))
    if (client.get("hires") or 0) == 0 and (client.get("total_spent") or 0) == 0:
        flags.append(RiskFlag(code="new_client", severity="low", quote=""))
    sig = injection_signals(text)
    guard = await classify_injection(job.get("description", "")[-2000:])
    injection = bool(sig) or (guard is not None and guard >= 0.5)
    if injection:
        flags.append(RiskFlag(code="prompt_injection", severity="medium",
                              quote=(sig[0] if sig else f"prompt-guard score {guard:.2f}")[:80]))
    score, is_scam = combine(flags, cq, injection, None)
    explanation = "; ".join(cq.notes) or "no client concerns"
    ambiguous = 0.2 <= score < 0.75 or (cq.payment_verified is False and not is_scam)
    if use_llm == "always" or (use_llm == "ambiguous" and ambiguous):
        try:
            res = await chat("risk_assessor", [
                {"role": "system", "content": load_prompt("risk_assessor")},
                {"role": "user", "content": f"Client history: {cq.model_dump_json()}\n\n" +
                 wrap_untrusted("job_post", text, 3000)}], schema=RiskLLMOut, max_tokens=500)
            llm: RiskLLMOut = res.parsed
            known = {f.code for f in flags}
            flags += [f for f in llm.flags if f.code not in known]
            score, is_scam = combine(flags, cq, injection, llm.is_scam)
            explanation = llm.explanation + (f" Client: {'; '.join(cq.notes)}." if cq.notes else "")
        except LLMError:
            explanation += " (LLM risk check unavailable; rule-based result)"
    level = "high" if score >= settings().thresholds["max_risk"] else ("medium" if score >= 0.3 else "low")
    return RiskReport(score=score, level=level, is_scam=is_scam, flags=flags, injection_detected=injection,
                      injection_score=guard, client=cq, explanation=explanation)


@gigbid_node("risk_assessor")
async def risk_assessor(state: dict, ctx: NodeContext) -> dict:
    job = state["job"]
    client = {}
    if job.get("client_id"):
        client = await ctx.tools("risk_assessor").call("marketplace", "get_client_history",
                                                       {"client_id": job["client_id"]})
    report = await assess(job, client, settings().raw.get("risk_llm", "ambiguous"))
    return {"risk": report.model_dump(), "client": client,
            "_summary": f"risk {report.score:.2f} ({report.level}){' SCAM' if report.is_scam else ''}"
                        f"{' injection' if report.injection_detected else ''}"}
