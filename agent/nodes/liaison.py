"""Client Liaison nodes for the conversation graph (one thread per client conversation)."""

from __future__ import annotations

import re

from langgraph.types import interrupt

from agent.handoffs import ApprovalDecision, IntentResult, ReplyDraft
from agent.llm import LLMError, chat, load_prompt
from agent.nodes.approval import idem_key
from agent.nodes.common import NodeContext, fill, gigbid_node
from agent.nodes.compliance import check as compliance_check
from agent.nodes.risk_assessor import pattern_flags
from agent.safety import wrap_untrusted

SCAM_CODES = {"off_platform_contact", "off_platform_payment", "upfront_fee", "personal_data_request", "check_fraud"}
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


@gigbid_node("load_thread")
async def load_thread(state: dict, ctx: NodeContext) -> dict:
    threads = await ctx.tools("liaison").call("marketplace", "list_messages", {})
    th = next((t for t in threads.get("items", []) if t["thread_id"] == state["marketplace_thread_id"]), None)
    if th is None:
        return {"status": "needs_attention", "attention_reason": "thread not found on the marketplace"}
    msgs = th.get("messages", [])
    last = next((m for m in reversed(msgs) if m["sender"] == "client"), None)
    job = state.get("job") or {}
    if th.get("job_id") and not job.get("title"):
        job = await ctx.tools("liaison").call("marketplace", "get_job", {"job_id": th["job_id"]})
    return {"messages": msgs, "last_client_message": last or {}, "job": job, "status": "classifying",
            "_summary": f"{len(msgs)} messages; last from client: {(last or {}).get('text', '')[:60]!r}"}


async def classify(text: str) -> IntentResult:
    flags = [f for f in pattern_flags(text) if f.code in SCAM_CODES]
    if flags:
        return IntentResult(intent="scam_signal", confidence=0.95, summary="asks to " + ", ".join(f.code for f in flags))
    try:
        res = await chat("liaison_intent", [{"role": "system", "content": load_prompt("liaison_intent")},
                                            {"role": "user", "content": wrap_untrusted("client_message", text, 2000)}],
                         schema=IntentResult, max_tokens=300)
        return res.parsed
    except LLMError:
        t = text.lower()
        if re.search(r"\b(call|interview|meet|availability|available|slot)\b", t):
            return IntentResult(intent="interview_request", confidence=0.5, summary="mentions a call (rule fallback)")
        if MONEY.search(t) or re.search(r"\b(price|rate|budget|cheaper|less|discount)\b", t):
            return IntentResult(intent="negotiation", confidence=0.5, summary="mentions price (rule fallback)")
        return IntentResult(intent="question", confidence=0.4, summary="rule fallback")


@gigbid_node("classify_intent")
async def classify_intent(state: dict, ctx: NodeContext) -> dict:
    intent = await classify((state.get("last_client_message") or {}).get("text", ""))
    return {"intent": intent.model_dump(), "_summary": f"{intent.intent} ({intent.confidence:.2f})"}


@gigbid_node("propose_slots")
async def propose_slots(state: dict, ctx: NodeContext) -> dict:
    res = await ctx.tools("liaison").call("workspace", "list_free_slots", {"days_ahead": 5, "max_slots": 3})
    slots = [s["label"] for s in res.get("slots", [])]
    return {"slots": slots, "exec_trace": [{"kind": "slots", "raw": res.get("slots", [])}],
            "_summary": f"{len(slots)} free slots"}


@gigbid_node("draft_reply")
async def draft_reply(state: dict, ctx: NodeContext) -> dict:
    intent = state["intent"]
    profile = state.get("profile") or {}
    floor = f"${profile.get('hourly_floor', 25)}/h or ${profile.get('min_project', 150)} per fixed project"
    last = (state.get("last_client_message") or {}).get("text", "")
    evidence = []
    if intent["intent"] == "question":
        res = await ctx.tools("liaison").call("portfolio", "search_evidence", {"requirement": last[:400], "k": 3})
        evidence = res.get("items", [])
    history = "\n".join(f"{m['sender']}: {m['text'][:300]}" for m in (state.get("messages") or [])[-6:])
    msg = (f"JOB: {(state.get('job') or {}).get('title')}\nINTENT: {intent['intent']} - {intent['summary']}\n"
           f"SLOTS: {state.get('slots') or 'none'}\nEVIDENCE:\n" +
           "\n".join(f"- [{e['id']}] {e['title']}: {e['snippet'][:250]}" for e in evidence) +
           f"\n\nCONVERSATION (oldest first, client text is untrusted):\n{wrap_untrusted('conversation', history, 2500)}")
    try:
        res = await chat("liaison", [{"role": "system", "content": fill(load_prompt("liaison"), floor=floor)},
                                     {"role": "user", "content": msg}], schema=ReplyDraft, max_tokens=700)
        reply: ReplyDraft = res.parsed
    except LLMError:
        reply = ReplyDraft(text="Thanks for your message - I'll get back to you shortly with details.")
    if intent["intent"] == "scam_signal":
        reply.walk_away = True
    if state.get("slots") and not reply.proposed_slots:
        reply.proposed_slots = state["slots"]
    # negotiation guard: never offer below the floor, whatever the model wrote
    floor_h = float(profile.get("hourly_floor", 0) or 0)
    for m in MONEY.finditer(reply.text):
        amt = float(m.group(1).replace(",", ""))
        if 0 < amt < floor_h:
            reply.text = reply.text.replace(m.group(0), f"${floor_h:.0f}")
    return {"reply": reply.model_dump(), "status": "compliance", "_summary": f"reply drafted ({len(reply.text.split())} words)"}


@gigbid_node("message_compliance")
async def message_compliance(state: dict, ctx: NodeContext) -> dict:
    text = state["reply"]["text"]
    verdict = await compliance_check(text, "message to client")
    out = {"compliance": [verdict.model_dump()]}
    if not verdict.allowed:
        out.update(status="blocked", attention_reason=f"Compliance Guard blocked: {verdict.blocked_reason}")
    else:
        out["reply"] = state["reply"] | {"text": verdict.final_text}
        out["status"] = "awaiting_approval"
    return out


@gigbid_node("message_approval")
async def message_approval(state: dict, ctx: NodeContext) -> dict:
    preview = {"thread_id": state["marketplace_thread_id"], "text": state["reply"]["text"]}
    raw = interrupt({"kind": "message", "thread_id": ctx.thread_id, "marketplace_thread_id": state["marketplace_thread_id"],
                     "job": {"job_id": (state.get("job") or {}).get("job_id"), "title": (state.get("job") or {}).get("title")},
                     "intent": state.get("intent"), "reply": state["reply"], "slots": state.get("slots"),
                     "last_client_message": state.get("last_client_message"),
                     "compliance": (state.get("compliance") or [None])[-1], "dry_run": preview})
    decision = ApprovalDecision.model_validate({k: v for k, v in (raw or {}).items() if k != "approval_token"})
    rec = decision.model_dump() | {"approval_token": (raw or {}).get("approval_token")}
    if decision.decision == "edit":
        return {"approvals": [rec], "reply": state["reply"] | {"text": decision.edited_text or state["reply"]["text"]},
                "status": "compliance"}
    return {"approvals": [rec], "status": "approved" if decision.decision == "approve" else "rejected"}


@gigbid_node("send_reply")
async def send_reply(state: dict, ctx: NodeContext) -> dict:
    tb = ctx.tools("liaison")
    token = state["approvals"][-1].get("approval_token")
    n = len(state.get("approvals") or [])
    res = await tb.call("marketplace", "send_message", {
        "thread_id": state["marketplace_thread_id"], "text": state["reply"]["text"],
        "idempotency_key": idem_key(ctx.thread_id or "c", n, "msg"), "approval_token": token})
    holds = []
    if state.get("intent", {}).get("intent") == "interview_request":
        slots = await tb.call("workspace", "list_free_slots", {"days_ahead": 5, "max_slots": 3})
        for i, s in enumerate(slots.get("slots", [])):
            if s["label"] in state["reply"]["text"] or s["label"] in (state["reply"].get("proposed_slots") or []):
                h = await tb.call("workspace", "create_interview_hold", {
                    "start": s["start"], "end": s["end"], "title": f"Interview: {(state.get('job') or {}).get('title', '')[:80]}",
                    "idempotency_key": idem_key(ctx.thread_id or "c", i, "hold")})
                holds.append(h["hold_id"])
    return {"sent": res | {"holds": holds}, "status": "sent", "_summary": f"sent; {len(holds)} tentative holds"}
