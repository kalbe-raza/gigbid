"""Fit Matcher: map every requirement to portfolio evidence (through the Portfolio MCP server) and score fit.

Runs in parallel with the Job Analyst, so it decomposes the post itself (specific skills + requirement sentences,
the same decomposition V1 uses) rather than waiting for the JobSpec. Deterministic; no LLM tokens.

  requirement status: proven  - top evidence names the requirement's skill (text or derived skill metadata), or
                                shares >= 30% of the requirement's content words
                      partial - evidence came back but does not demonstrably cover it
                      missing - nothing relevant
  fit = 0.5 * requirement coverage (must = 1, nice = 0.5)  +  0.5 * coverage of the job's specific listed skills
"""

from __future__ import annotations

import asyncio

from agent.handoffs import EvidenceRef, FitReport, RequirementMatch
from agent.nodes.common import NodeContext, gigbid_node
from agent.settings import settings
from rag.skills import GENERIC_SKILLS, canon, canon_set
from rag.variants.base import heuristic_requirements, tokens_overlap
from rag.variants.v8_corrective import key_terms

EVIDENCE_TYPES = ["case_study", "readme", "code", "review", "cv"]


def requirement_status(req: str, items: list[dict]) -> str:
    if not items:
        return "missing"
    terms = key_terms(req)
    for it in items[:3]:
        text = (it.get("snippet") or "").lower()
        skills = canon_set(it.get("skills") or [])
        if terms and any(t in skills or t in text for t in terms):
            return "proven"
        if not terms and tokens_overlap(req, it.get("snippet", "")) >= 0.3:
            return "proven"
    return "partial"


def score_fit(matrix: list[RequirementMatch], job_skills: list[str], portfolio_skills: set[str],
              priorities: dict[str, str]) -> tuple[float, list[str], list[str]]:
    w_total = w_got = 0.0
    for m in matrix:
        w = 1.0 if priorities.get(m.requirement, "must") == "must" else 0.5
        w_total += w
        w_got += w * {"proven": 1.0, "partial": 0.4, "missing": 0.0}[m.status]
    req_cov = w_got / w_total if w_total else 0.0
    specific = [s for s in canon_set(job_skills) if s not in GENERIC_SKILLS]
    basis = specific or list(canon_set(job_skills))
    matched = sorted(s for s in basis if s in portfolio_skills)
    missing = sorted(s for s in basis if s not in portfolio_skills)
    if basis:
        fit = 0.5 * req_cov + 0.5 * (len(matched) / len(basis))
    else:
        fit = req_cov
    return round(fit, 3), matched, missing


async def match(job: dict, toolbox, requirements: list[str] | None = None, k: int = 3) -> FitReport:
    reqs = requirements or heuristic_requirements(job)
    priorities = {r: ("must" if canon(r.removeprefix("experience with ")) not in GENERIC_SKILLS else "nice") for r in reqs}

    async def one(r: str) -> tuple[str, list[dict], str]:
        res = await toolbox.call("portfolio", "search_evidence",
                                 {"requirement": r, "k": k, "filters": {"types": EVIDENCE_TYPES}})
        return r, res.get("items", []), res.get("variant", "")

    results = await asyncio.gather(*(one(r) for r in reqs))
    skills_res = await toolbox.call("portfolio", "list_skills", {})
    portfolio_skills = {canon(s["skill"]) for s in skills_res.get("skills", [])}
    matrix, variant = [], ""
    for r, items, variant in results:
        status = requirement_status(r, items)
        matrix.append(RequirementMatch(
            requirement=r, skill=next(iter(key_terms(r)), None), status=status,
            evidence=[EvidenceRef(doc_id=i["id"], title=i.get("title", ""), type=i.get("type", ""),
                                  snippet=i.get("snippet", "")[:400], score=i.get("score", 0.0))
                      for i in (items if status != "missing" else [])]))
    fit, matched, missing = score_fit(matrix, job.get("skills", []), portfolio_skills, priorities)
    reasons = [f"{sum(m.status == 'proven' for m in matrix)}/{len(matrix)} requirements proven by portfolio evidence"]
    if matched:
        reasons.append("portfolio shows: " + ", ".join(matched[:6]))
    if missing:
        reasons.append("no evidence for: " + ", ".join(missing[:6]))
    for m in matrix:
        if m.status == "proven" and m.evidence:
            reasons.append(f"'{m.requirement[:60]}' <- {m.evidence[0].doc_id}")
    return FitReport(score=fit, matrix=matrix, matched_skills=matched, missing_skills=missing, reasons=reasons[:8],
                     unsupported_requirements=[m.requirement for m in matrix if m.status == "missing"],
                     retrieval_variant=variant)


@gigbid_node("fit_matcher")
async def fit_matcher(state: dict, ctx: NodeContext) -> dict:
    report = await match(state["job"], ctx.tools("fit_matcher"), k=settings().rag.get("k_per_requirement", 3))
    evidence = [e.model_dump() | {"requirement": m.requirement} for m in report.matrix for e in m.evidence]
    return {"fit": report.model_dump(), "evidence": evidence,
            "_summary": f"fit {report.score:.2f}; {len(report.unsupported_requirements)} requirements unsupported"}
