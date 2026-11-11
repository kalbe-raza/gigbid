"""Portfolio tool implementations (pure functions over the corpus; server.py registers them with MCP)."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from mcp_servers.portfolio.corpus import documents, retriever
from rag.skills import canon

EvidenceType = Literal["cv", "case_study", "readme", "code", "review", "proposal"]


class EvidenceFilters(BaseModel):
    types: list[EvidenceType] | None = Field(default=None, description="Restrict to these evidence types")
    project: str | None = Field(default=None, max_length=80)
    since_year: int | None = Field(default=None, ge=1990, le=2100)
    outcome: Literal["won", "replied", "ignored"] | None = Field(default=None, description="Only for proposals")


class EvidenceItem(BaseModel):
    id: str = Field(description="Evidence id to cite, e.g. case:helpdesk-rag or repo:trackline")
    type: str
    source: str
    title: str
    project: str | None = None
    date: str | None = None
    outcome: str | None = None
    client_industry: str | None = None
    skills: list[str] = Field(default_factory=list)
    snippet: str
    score: float


class EvidenceResult(BaseModel):
    requirement: str
    items: list[EvidenceItem]
    variant: str


class CaseStudy(BaseModel):
    id: str
    title: str
    project: str | None
    client_industry: str | None
    date: str | None
    skills: list[str]
    text: str
    related: list[str] = Field(default_factory=list, description="Other evidence ids for the same project")


class SkillCount(BaseModel):
    skill: str
    evidence_count: int
    projects: list[str]


class SkillList(BaseModel):
    corpus: str
    skills: list[SkillCount]


def search_evidence(corpus: str, requirement: str, k: int = 5, filters: EvidenceFilters | None = None) -> EvidenceResult:
    r = retriever(corpus)
    f: dict = {}
    if filters and filters.types:
        f["type"] = list(filters.types)
    if filters and filters.project:
        f["project"] = filters.project
    if filters and filters.outcome:
        f["outcome"] = filters.outcome
    job = {"title": requirement, "description": requirement, "skills": []}
    res = r.retrieve(job, requirements=[requirement], k=max(k * 3, 10), filters=f or None)
    docs = documents(corpus)
    items, seen = [], set()
    for e in res.items:
        if e.doc_id in seen:
            continue
        d = docs.get(e.chunk_id) or docs.get(e.doc_id)
        year = (e.meta.get("date") or "")[:4]
        if filters and filters.since_year and year.isdigit() and int(year) < filters.since_year:
            continue
        seen.add(e.doc_id)
        items.append(EvidenceItem(id=e.doc_id, type=e.type, source=(e.meta.get("source") or "").split("/data/")[-1],
                                  title=e.title, project=e.project, date=e.meta.get("date"),
                                  outcome=d.outcome if d else None, client_industry=e.meta.get("client_industry"),
                                  skills=list(e.meta.get("skills") or [])[:12], snippet=e.text[:600],
                                  score=round(e.score, 4)))
        if len(items) >= k:
            break
    return EvidenceResult(requirement=requirement, items=items, variant=r.cfg.name)


def get_case_study(corpus: str, case_id: str) -> CaseStudy:
    docs = documents(corpus)
    key = case_id if ":" in case_id else f"case:{case_id}"
    d = docs.get(key)
    if d is None:
        raise KeyError(f"no case study {case_id!r} in this portfolio")
    related = sorted(x for x, doc in docs.items() if doc.project == d.project and x != key and doc.type != "proposal")
    return CaseStudy(id=key, title=d.title, project=d.project, client_industry=d.client_industry, date=d.date,
                     skills=d.skills, text=d.text, related=related[:12])


def list_skills(corpus: str) -> SkillList:
    counts: Counter = Counter()
    projects: dict[str, set] = {}
    for d in documents(corpus).values():
        if d.type == "proposal":
            continue  # a proposal claims a skill, it does not prove it
        for s in d.skills:
            c = canon(s)
            counts[c] += 1
            if d.project:
                projects.setdefault(c, set()).add(d.project)
    return SkillList(corpus=corpus, skills=[SkillCount(skill=s, evidence_count=n, projects=sorted(projects.get(s, set()))[:8])
                                            for s, n in counts.most_common()])
