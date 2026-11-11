"""Critic: grounding, personalisation, length and opening checks; sends the draft back at most twice (config)."""

from __future__ import annotations

import re

from agent.handoffs import Critique, CritiqueIssue
from agent.llm import LLMError, chat, load_prompt
from agent.nodes.common import NodeContext, fill, gigbid_node
from agent.safety import wrap_untrusted
from agent.settings import settings

GENERIC = re.compile(r"^\s*(dear (sir|madam|hiring manager)|hello,? i am|hi,? i am|i am (very )?passionate|"
                     r"i am an expert|i have read your (job|post))", re.I)


def precheck(draft: dict, verification: dict | None) -> list[CritiqueIssue]:
    issues = []
    lo, hi = settings()["proposal"]["target_words"]
    wc = draft.get("word_count") or len(draft["cover_letter"].split())
    if wc < lo * 0.8 or wc > hi * 1.15:
        issues.append(CritiqueIssue(kind="length", detail=f"{wc} words; target {lo}-{hi}"))
    if GENERIC.search(draft["cover_letter"]):
        issues.append(CritiqueIssue(kind="generic_opening", detail="opens with a generic line", paragraph=1))
    limit = settings()["proposal"].get("max_cover_letter_chars")
    if limit and len(draft["cover_letter"]) > limit:
        issues.append(CritiqueIssue(kind="length", detail=f"{len(draft['cover_letter'])} chars; policy max {limit}"))
    partly = [c for c in (verification or {}).get("claims", []) if c["status"] == "partly_supported"]
    for c in partly[:2]:
        issues.append(CritiqueIssue(kind="grounding", detail=f"only partly supported: {c['text'][:90]}"))
    return issues


@gigbid_node("critic")
async def critic(state: dict, ctx: NodeContext) -> dict:
    draft = state["drafts"][-1]
    ver = (state.get("verification") or [None])[-1]
    issues = precheck(draft, ver)
    lo, hi = settings()["proposal"]["target_words"]
    try:
        res = await chat("critic", [
            {"role": "system", "content": fill(load_prompt("critic"), min_words=lo, max_words=hi)},
            {"role": "user", "content":
                f"JOB SPEC: {state['job_spec'].get('summary')}\nMUST requirements: "
                + "; ".join(r["text"] for r in state["job_spec"].get("requirements", []) if r.get("priority") == "must")
                + f"\nScreening questions: {state['job_spec'].get('screening_questions')}\n"
                f"VERIFICATION: {ver['total_claims'] if ver else 0} claims, unsupported after verification "
                f"{ver['unsupported_after'] if ver else 0}\n\nDRAFT:\n{draft['cover_letter']}\n\nANSWERS:\n"
                + "\n".join(f"Q: {a['question']}\nA: {a['answer']}" for a in draft.get("screening_answers", []))
                + "\n\n" + wrap_untrusted("job_post", state["job"].get("description", ""), 1500)}],
            schema=Critique, max_tokens=700)
        c: Critique = res.parsed
    except LLMError:
        c = Critique(approve=not issues, score=7.0 if not issues else 5.0, issues=[],
                     instructions="(LLM critic unavailable; deterministic checks only)")
    c.issues = issues + c.issues
    hard = any(i.kind in ("generic_opening", "grounding") for i in issues) or any(i.kind == "length" and "policy" in i.detail
                                                                                  for i in issues)
    if hard:
        c.approve = False
        c.instructions = (c.instructions + " " + "; ".join(i.detail for i in issues)).strip()
    revision = int(state.get("revision") or 0)
    send_back = not c.approve and revision < settings().budgets["max_revisions"]
    return {"critiques": [c.model_dump()], "revision": revision + 1 if send_back else revision,
            "status": "revising" if send_back else "compliance",
            "_summary": f"score {c.score:.1f}, {'approved' if c.approve else 'needs work'}"
                        f"{' -> back to writer' if send_back else ''}"}
