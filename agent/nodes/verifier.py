"""Claim verification node (B3): runs rag.verify on the newest draft BEFORE the Critic sees it."""

from __future__ import annotations

from agent.handoffs import ProposalDraft, ScreeningAnswer, VerificationReport
from agent.nodes.common import NodeContext, gigbid_node
from agent.settings import settings
from rag.verify import CITE, verify_text


def evidence_pool(state: dict) -> list[dict]:
    pool = []
    for e in state.get("evidence") or []:
        pool.append({"doc_id": e["doc_id"], "text": e.get("snippet") or e.get("text", ""), "skills": e.get("skills", []),
                     "type": e.get("type")})
    for m in (state.get("fit") or {}).get("matrix", []):
        for e in m.get("evidence", []):
            pool.append({"doc_id": e["doc_id"], "text": e.get("snippet", ""), "type": e.get("type")})
    return pool


async def verify_draft(draft: dict, pool: list[dict]) -> tuple[ProposalDraft, VerificationReport]:
    thr = settings().thresholds["claim_support_min"]
    letter, claims, rep = await verify_text(draft["cover_letter"], pool, thr)
    answers, all_claims = [], list(claims)
    before, after = rep.unsupported_before, rep.unsupported_after
    for a in draft.get("screening_answers", []):
        text, c2, r2 = await verify_text(a["answer"], pool, thr)
        answers.append(ScreeningAnswer(question=a["question"], answer=text or "I don't have a past project that shows "
                                       "this directly; I'd be glad to explain how I would approach it."))
        all_claims += c2
        before += r2.unsupported_before
        after += r2.unsupported_after
    report = VerificationReport(claims=all_claims, unsupported_before=before, unsupported_after=after,
                                total_claims=len(all_claims), llm_checked=rep.llm_checked)
    new = ProposalDraft(version=draft["version"], cover_letter=letter, screening_answers=answers, claims=all_claims,
                        evidence_ids=sorted(set(CITE.findall(letter))), word_count=len(letter.split()),
                        source="verifier")
    return new, report


async def claim_evidence(draft: dict, toolbox, limit: int = 6) -> list[dict]:
    """Look up evidence for each claim (not just the Fit Matcher's snippets) so true claims are not removed."""
    from rag.verify import is_claim, split_sentences

    texts = [draft["cover_letter"]] + [a["answer"] for a in draft.get("screening_answers", [])]
    claims = [s for t in texts for s in split_sentences(t) if is_claim(s)][:limit]
    pool = []
    for c in claims:
        res = await toolbox.call("portfolio", "search_evidence", {"requirement": CITE.sub("", c)[:480], "k": 3})
        pool += [{"doc_id": i["id"], "text": i["snippet"], "skills": i.get("skills", []), "type": i["type"]}
                 for i in res.get("items", [])]
    return pool


@gigbid_node("verifier")
async def verifier(state: dict, ctx: NodeContext) -> dict:
    draft = state["drafts"][-1]
    pool = evidence_pool(state) + await claim_evidence(draft, ctx.tools("verifier"))
    new, report = await verify_draft(draft, pool)
    return {"drafts": [new.model_dump()], "verification": [report.model_dump()],
            "_summary": f"{report.total_claims} claims; unsupported {report.unsupported_before} -> {report.unsupported_after}"}
