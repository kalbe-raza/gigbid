"""Compliance Guard: enforce the policy pack on every outgoing text (proposals AND client messages).

1. deterministic: contact details, URLs, off-platform payment words and guarantee phrases (patterns in config) are
   removed sentence by sentence; the platform's length limit (config, set by the change request) is enforced
2. policy model (gpt-oss-safeguard-20b, built for policy-following classification) checks what regexes cannot see
3. block (with a reason shown in the UI) when a violation cannot be fixed by deleting a sentence
"""

from __future__ import annotations

import re

from agent.handoffs import ComplianceLLMOut, ComplianceVerdict, Violation
from agent.llm import LLMError, chat, load_prompt
from agent.nodes.common import NodeContext, fill, gigbid_node
from agent.settings import ROOT, settings
from rag.verify import SENT

_POLICY_CACHE: str | None = None


def policy_text() -> str:
    global _POLICY_CACHE
    if _POLICY_CACHE is None:
        _POLICY_CACHE = (ROOT / settings()["compliance"]["policy_pack"]).read_text()
    return _POLICY_CACHE


def _is_sentence(fix: str) -> bool:
    f = fix.strip()
    return bool(f) and f[0].isupper() and f[-1] in ".!?" and not re.match(
        r"^(delete|remove|provide|rephrase|replace|rewrite|add|cite|clarify|consider|change)\b", f, re.I)


def deterministic(text: str) -> tuple[str, list[Violation], list[str]]:
    pats = {k: re.compile(v, re.I) for k, v in settings()["compliance"]["banned_patterns"].items()}
    violations, redactions, kept_paras = [], [], []
    for para in re.split(r"\n\s*\n", text.strip()):
        kept = []
        for s in SENT.split(para.strip()):
            hit = next((k for k, p in pats.items() if p.search(s)), None)
            if hit:
                violations.append(Violation(rule=hit, text=s[:200], fix="delete"))
                redactions.append(s)
            else:
                kept.append(s)
        if kept:
            kept_paras.append(" ".join(kept))
    out = "\n\n".join(kept_paras)
    limit = settings()["proposal"].get("max_cover_letter_chars")
    if limit and len(out) > limit:
        cut = out[:limit]
        cut = cut[:max(cut.rfind(". "), cut.rfind("? ")) + 1] or cut
        violations.append(Violation(rule="max_cover_letter_chars", text=f"{len(out)} chars", fix=f"trimmed to {len(cut)}"))
        out = cut
    return out, violations, redactions


async def check(text: str, kind: str = "proposal", use_llm: bool = True) -> ComplianceVerdict:
    cleaned, violations, redactions = deterministic(text)
    checked = ["patterns"]
    blocked, reason = False, None
    if use_llm and cleaned.strip():
        try:
            res = await chat("compliance", [
                {"role": "system", "content": fill(load_prompt("compliance"), policy=policy_text())},
                {"role": "user", "content": f"Outgoing {kind}:\n<<<\n{cleaned}\n>>>"}], schema=ComplianceLLMOut,
                max_tokens=600)
            llm: ComplianceLLMOut = res.parsed
            checked.append(res.model)
            for v in llm.violations:
                violations.append(v)
                if not v.text or v.text not in cleaned:
                    continue
                if v.fix.strip().lower() in ("delete", "remove", ""):
                    cleaned = re.sub(r"\s*" + re.escape(v.text) + r"\s*", " ", cleaned).strip()
                    redactions.append(v.text)
                elif _is_sentence(v.fix) and not deterministic(v.fix)[1]:
                    cleaned = cleaned.replace(v.text, v.fix)
                # anything else (instructions such as "provide evidence") is left for the human, shown in the UI
            if llm.block:
                blocked, reason = True, llm.reason or "policy model blocked this text"
        except LLMError:
            checked.append("policy-model-unavailable")
    if not cleaned.strip():
        blocked, reason = True, reason or "nothing left after removing policy violations"
    return ComplianceVerdict(allowed=not blocked, blocked_reason=reason, violations=violations, redactions=redactions,
                             final_text=cleaned, checked_by=checked)


@gigbid_node("compliance")
async def compliance(state: dict, ctx: NodeContext) -> dict:
    draft = dict(state["drafts"][-1])
    verdict = await check(draft["cover_letter"], "proposal")
    answers = []
    for a in draft.get("screening_answers", []):
        v2 = await check(a["answer"], "screening answer", use_llm=False)
        answers.append({"question": a["question"], "answer": v2.final_text})
        verdict.violations += v2.violations
    out = {"compliance": [verdict.model_dump()]}
    if not verdict.allowed:
        out.update(status="blocked", attention_reason=f"Compliance Guard blocked: {verdict.blocked_reason}")
    else:
        if verdict.final_text != draft["cover_letter"] or answers != draft.get("screening_answers"):
            draft.update(cover_letter=verdict.final_text, screening_answers=answers, source="compliance",
                         word_count=len(verdict.final_text.split()))
            out["drafts"] = [draft]
        out["status"] = "awaiting_approval"
    out["_summary"] = (f"{'BLOCKED' if not verdict.allowed else 'ok'}; {len(verdict.violations)} violations fixed"
                       if verdict.allowed else f"BLOCKED: {verdict.blocked_reason}")
    return out
