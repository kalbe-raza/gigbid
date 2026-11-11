"""V8 (agentic group): corrective RAG as a small LangGraph loop.

    retrieve -> grade -> (weak requirements?) -> rewrite -> retrieve -> grade -> finalize
                                     \\-> finalize (nothing weak, or retry budget used)

Grading is deterministic and cheap: a requirement is SUPPORTED when its best evidence passes the cross-encoder
threshold AND the evidence actually mentions the requirement's key skill (text or derived skill metadata).
Weak requirements are re-queried once with alias/related-term expansion. Requirements still unsupported are
returned in `unsupported`, and the writer is told it must not claim them ("refuse to claim when none").
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from rag.skills import ALIASES, canon, canon_set
from rag.variants.base import Evidence, RetrievalResult, VariantConfig, heuristic_requirements, rrf

CONFIG = VariantConfig(name="v8_corrective", chunking="structured", query="decomposed", retrieval="hybrid",
                       rerank="cross", outcome=True, corrective=True, min_support=-2.0)

RELATED = {
    "celery": ["background task queue", "async worker", "retry"], "dbt": ["sql transformations", "data warehouse model"],
    "pinecone": ["vector index", "vector database", "embeddings"], "power bi": ["dax", "dashboard", "power query"],
    "react": ["component", "front-end", "jsx"], "django": ["python web framework", "django rest framework"],
    "langchain": ["llm application", "retrieval augmented", "rag"], "computer vision": ["image", "detection", "opencv"],
    "natural language processing": ["text classification", "transformer", "sentiment"],
    "time series forecasting": ["forecast", "demand", "seasonal"], "tableau": ["dashboard", "visualization"],
    "stripe": ["payments", "subscription billing", "checkout"], "docker": ["container", "dockerfile"],
    "sql": ["query", "postgresql", "warehouse"], "microsoft excel": ["spreadsheet", "financial model", "vba"],
}
_REVERSE: dict[str, list[str]] = {}
for _a, _t in ALIASES.items():
    _REVERSE.setdefault(_t, []).append(_a)

MAX_RETRIES = 1


def key_terms(requirement: str) -> set[str]:
    m = re.match(r"experience with (.+)$", requirement)
    if m:
        return {canon(m.group(1))}
    from rag.skills import mentions
    from rag.vocab import VOCAB

    return {canon(s) for s in mentions(requirement, VOCAB)}


def expand(requirement: str) -> str:
    extra = []
    for t in key_terms(requirement):
        extra += RELATED.get(t, []) + _REVERSE.get(t, [])[:2]
    return requirement + (" (" + ", ".join(dict.fromkeys(extra)) + ")" if extra else " project examples")


def supported(req: str, ev: list[Evidence], threshold: float) -> bool:
    if not ev:
        return False
    terms = key_terms(req)
    for e in ev[:3]:
        if e.score < threshold:
            continue
        if not terms:
            return True
        text = e.text.lower()
        skills = canon_set(e.meta.get("skills") or [])
        if any(t in skills or t in text for t in terms):
            return True
    return False


class CState(TypedDict, total=False):
    requirements: list[str]
    queries: dict[str, str]
    results: Annotated[dict, operator.or_]
    weak: list[str]
    attempts: int


def corrective_retrieve(pipe, job: dict, requirements: list[str] | None, k: int, filters: dict | None) -> RetrievalResult:
    reqs = requirements or heuristic_requirements(job)
    thr = pipe.cfg.min_support

    def retrieve(state: CState) -> dict:
        todo = state.get("weak") or state["requirements"]
        out = {}
        for r in todo:
            q = state["queries"].get(r, r)
            out[r] = pipe._to_evidence(pipe._one(q, k, filters), 5)
        return {"results": out}

    def grade(state: CState) -> dict:
        weak = [r for r in state["requirements"] if not supported(r, state["results"].get(r, []), thr)]
        return {"weak": weak}

    def rewrite(state: CState) -> dict:
        qs = dict(state["queries"])
        for r in state["weak"]:
            qs[r] = expand(r)
        return {"queries": qs, "attempts": state.get("attempts", 0) + 1}

    def route(state: CState) -> str:
        return "rewrite" if state["weak"] and state.get("attempts", 0) < MAX_RETRIES else "finalize"

    g = StateGraph(CState)
    g.add_node("retrieve", retrieve)
    g.add_node("grade", grade)
    g.add_node("rewrite", rewrite)
    g.add_node("finalize", lambda s: {})
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", route, {"rewrite": "rewrite", "finalize": "finalize"})
    g.add_edge("rewrite", "retrieve")
    g.add_edge("finalize", END)
    final = g.compile().invoke({"requirements": reqs, "queries": {r: r for r in reqs}, "results": {}, "attempts": 0})

    per_req = {r: final["results"].get(r, []) for r in reqs}
    unsupported = [r for r in reqs if r in (final.get("weak") or [])]
    good = {r: evs for r, evs in per_req.items() if r not in unsupported}
    fused = rrf([[(e.chunk_id, e.score) for e in evs] for evs in good.values()]) if good else {}
    lookup = {e.chunk_id: e for evs in good.values() for e in evs}
    items = [lookup[c] for c, _ in sorted(fused.items(), key=lambda x: -x[1])][:k]
    return RetrievalResult(items=items, per_requirement=per_req, unsupported=unsupported,
                           trace={"retries": final.get("attempts", 0), "weak_after": unsupported})
