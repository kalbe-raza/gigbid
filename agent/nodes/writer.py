"""Proposal Writer: cover letter + screening answers where every claim cites portfolio evidence.

Inputs it is given (and nothing else): the JobSpec, the requirement->evidence matrix from the Fit Matcher, evidence for
each screening question (Portfolio MCP), the price, the requirements it must NOT claim, promoted style rules and
outcome insights from long-term memory, winning-proposal exemplars, and the Critic's instructions on a revision.
"""

from __future__ import annotations

from agent.handoffs import ProposalDraft, WriterOut
from agent.llm import chat, load_prompt
from agent.memory import store as memory
from agent.nodes.common import NodeContext, fill, gigbid_node
from agent.safety import wrap_untrusted
from agent.settings import settings
from rag.verify import CITE


def experiment_arm(state: dict) -> str | None:
    """X2: deterministic arm per job (or forced by the A/B runner), recorded with the draft and the pipeline card."""
    import hashlib

    cfg = (settings().raw.get("experiments") or {}).get("proposal_ab") or {}
    if state.get("experiment_arm"):
        return state["experiment_arm"]
    if not cfg.get("enabled"):
        return None
    arms = cfg.get("arms") or ["A", "B"]
    return arms[int(hashlib.sha1(state["job"]["job_id"].encode()).hexdigest(), 16) % len(arms)]


def evidence_block(evidence: list[dict], limit: int = 12) -> tuple[str, list[dict]]:
    seen, rows = set(), []
    for e in evidence:
        if e["doc_id"] in seen or e.get("type") == "proposal":
            continue
        seen.add(e["doc_id"])
        rows.append(e)
        if len(rows) >= limit:
            break
    text = "\n".join(f"- [{e['doc_id']}] ({e.get('type')}) {e.get('title', '')}: {(e.get('snippet') or e.get('text', ''))[:350]}"
                     for e in rows)
    return text, rows


async def write(state: dict, ctx: NodeContext) -> tuple[ProposalDraft, list[dict]]:
    spec, fit, price = state["job_spec"], state["fit"], state["price"]
    evidence = list(state.get("evidence") or [])
    tb = ctx.tools("writer")
    for q in spec.get("screening_questions", [])[:3]:
        res = await tb.call("portfolio", "search_evidence", {"requirement": q, "k": 2})
        evidence += [{"doc_id": i["id"], "type": i["type"], "title": i["title"], "snippet": i["snippet"],
                      "skills": i.get("skills", []), "requirement": q} for i in res.get("items", [])]
    ev_text, ev_rows = evidence_block(evidence)
    cfg = settings()["proposal"]
    lo, hi = cfg["target_words"]
    length_rule = f"Cover letter: {lo}-{hi} words, 3 short paragraphs."
    if cfg.get("max_cover_letter_chars"):
        length_rule += f" It must stay under {cfg['max_cover_letter_chars']} characters (platform policy)."
    rules = await memory.style_rules(ctx.user_id)
    category = state["job"].get("category") or "general"
    insights = await memory.outcome_insights(ctx.user_id, category)
    style = ""
    if rules:
        style += "\nSTYLE RULES LEARNED FROM THE FREELANCER'S EDITS (follow them):\n" + "\n".join(f"- {r['rule']}" for r in rules)
    if insights:
        style += "\nWHAT HAS WORKED FOR THIS FREELANCER:\n" + "\n".join(f"- {i['insight']}" for i in insights[:4])
    arm = experiment_arm(state)
    if arm:
        style += "\n" + load_prompt(f"writer_strategy_{arm}").split("-->", 1)[-1].strip()
    system = fill(load_prompt("writer"), length_rule=length_rule, style_rules=style,
                  max_answer_words=cfg["max_screening_answer_words"])
    unsupported = fit.get("unsupported_requirements") or []
    parts = [
        f"JOB: {spec.get('title')}\nSummary: {spec.get('summary')}\nDeliverables: {'; '.join(spec.get('deliverables', []))}",
        "Requirements: " + "; ".join(r["text"] for r in spec.get("requirements", [])),
        "LISTED SKILLS: " + (", ".join(state["job"].get("skills") or []) or "none"),
        "Screening questions: " + (" | ".join(spec.get("screening_questions", [])) or "none"),
        f"PRICE: {price['bid_type']} ${price['amount']:,.0f}{'/h' if price['bid_type'] == 'hourly' else ''}",
        f"EVIDENCE (cite only these ids):\n{ev_text or '- none'}",
        "NOT SUPPORTED (do not claim): " + ("; ".join(unsupported) or "none"),
    ]
    if state.get("exemplars"):
        parts.append("PHRASING THAT GOT REPLIES BEFORE (style reference only, do not copy facts):\n" +
                     "\n".join(f"- {x['text'][:300]}" for x in state["exemplars"][:2]))
    if state.get("critiques") and state.get("revision", 0) > 0:
        last = state["critiques"][-1]
        prev = state["drafts"][-1]["cover_letter"]
        parts.append(f"PREVIOUS DRAFT:\n{prev}\n\nCRITIC SAYS (fix all of it): {last.get('instructions')}\n" +
                     "\n".join(f"- {i['kind']}: {i['detail']}" for i in last.get("issues", [])))
    parts.append(wrap_untrusted("job_post", state["job"].get("description", ""), 2500))
    res = await chat("writer", [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}],
                     schema=WriterOut, max_tokens=2200, temperature=0.4)
    out: WriterOut = res.parsed
    cited = sorted(set(CITE.findall(out.cover_letter + " ".join(a.answer for a in out.screening_answers))))
    draft = ProposalDraft(version=max([d["version"] for d in state.get("drafts") or []], default=0) + 1, cover_letter=out.cover_letter.strip(),
                          screening_answers=out.screening_answers, evidence_ids=cited,
                          word_count=len(out.cover_letter.split()), source="writer")
    return draft, evidence


@gigbid_node("writer")
async def writer(state: dict, ctx: NodeContext) -> dict:
    draft, evidence = await write(state, ctx)
    rules = await memory.style_rules(ctx.user_id)
    return {"drafts": [draft.model_dump()], "evidence": evidence,
            "memory_used": {"style_rules": [r["key"] for r in rules]}, "status": "drafting",
            "experiment_arm": experiment_arm(state),
            "_summary": f"draft v{draft.version}: {draft.word_count} words, {len(draft.evidence_ids)} citations"}
