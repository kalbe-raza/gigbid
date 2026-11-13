"""A configurable retrieval pipeline. Each variant file (v0_naive.py ... v8_corrective.py) is a preset of
these switches, so variants and ablations are the same code with one component turned off.

    chunking   fixed | structured
    query      whole (title + description) | decomposed (one query per requirement)
    retrieval  dense | bm25 | hybrid (dense + BM25 fused with reciprocal rank fusion)
    rerank     none | cross (cross-encoder over the fused candidates)
    outcome    False | True  (boost evidence from projects cited in proposals that won or got replies)
    corrective False | True  (LangGraph loop: grade evidence per requirement, re-query when weak, refuse when none)
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from rag.index.embeddings import rerank as cross_rerank
from rag.index.store import CorpusIndex, get_index, tokenize
from rag.skills import GENERIC_SKILLS, canon_set

EVIDENCE_TYPES = ["cv", "case_study", "readme", "code", "review"]


@dataclass
class VariantConfig:
    name: str
    chunking: str = "structured"
    query: str = "whole"
    retrieval: str = "dense"
    rerank: str = "none"
    outcome: bool = False
    corrective: bool = False
    k_candidates: int = 30
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    outcome_weight: float = 0.6
    min_support: float = 0.0  # corrective: rerank score below this counts as weak evidence


@dataclass
class Evidence:
    doc_id: str          # evaluation id: case:<p>, repo:<p>, review:<id>, cv:<corpus>
    chunk_id: str
    type: str
    project: str | None
    title: str
    text: str
    score: float
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["text"] = self.text[:1200]
        return d


@dataclass
class RetrievalResult:
    items: list[Evidence]
    per_requirement: dict[str, list[Evidence]] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    exemplars: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)

    def doc_ranking(self) -> list[str]:
        seen, out = set(), []
        for e in self.items:
            if e.doc_id not in seen:
                seen.add(e.doc_id)
                out.append(e.doc_id)
        return out


def eval_doc_id(doc_id: str) -> str:
    return doc_id.split("#", 1)[0]


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
REQ_CUES = re.compile(r"\b(need|needs|must|require|required|build|develop|create|implement|integrat|experience|"
                      r"looking for|should|expert|familiar|deliver|design|set up|automate|migrat|optimi)", re.I)


def heuristic_requirements(job: dict, limit: int = 6) -> list[str]:
    """Cheap requirement extraction (no LLM): specific skills first, then requirement-like sentences."""
    reqs: list[str] = []
    specific = [s for s in sorted(canon_set(job.get("skills", []))) if s not in GENERIC_SKILLS]  # sorted: sets iterate in hash order
    for s in specific[:4]:
        reqs.append(f"experience with {s}")
    for sent in SENT_SPLIT.split(job.get("description", "")):
        sent = sent.strip(" -•*\t")
        if 25 <= len(sent) <= 220 and REQ_CUES.search(sent) and len(reqs) < limit:
            reqs.append(sent)
    if not reqs:
        reqs = [job.get("title", "")]
    return reqs[:limit]


def rrf(rankings: list[list[tuple[str, float]]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (cid, _) in enumerate(ranking):
            fused[cid] += 1.0 / (k + rank + 1)
    return fused


class Pipeline:
    def __init__(self, cfg: VariantConfig, corpus: str, index: CorpusIndex | None = None,
                 proposal_index: CorpusIndex | None = None):
        self.cfg = cfg
        self.corpus = corpus
        self.index = index or get_index(corpus, cfg.chunking, cfg.embed_model)
        self.proposals = proposal_index
        self._prior: dict[str, float] | None = None

    # ------------------------------------------------------------------ building blocks
    def _search(self, query: str, k: int, filters: dict | None) -> list[tuple]:
        filt = {"type": EVIDENCE_TYPES, **(filters or {})}
        if self.cfg.retrieval == "dense":
            return self.index.dense(query, k, filt)
        if self.cfg.retrieval == "bm25":
            return self.index.bm25(query, k, filt)
        dense = self.index.dense(query, k, filt)
        sparse = self.index.bm25(query, k, filt)
        by_id = {c.chunk_id: c for c, _ in dense + sparse}
        fused = rrf([[(c.chunk_id, s) for c, s in dense], [(c.chunk_id, s) for c, s in sparse]])
        return sorted(((by_id[i], s) for i, s in fused.items()), key=lambda x: -x[1])[:k]

    def _rerank(self, query: str, cands: list[tuple]) -> list[tuple]:
        if self.cfg.rerank != "cross" or not cands:
            return cands
        scores = cross_rerank(query, [c.text[:1500] for c, _ in cands], self.cfg.rerank_model)
        return sorted(((c, s) for (c, _), s in zip(cands, scores, strict=True)), key=lambda x: -x[1])

    def project_prior(self) -> dict[str, float]:
        """Outcome prior per project from past proposals: (2*won + replied + 1) / (2*n + 2)."""
        if self._prior is None:
            counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for c in self.index.chunks:
                if c.meta.get("type") == "proposal" and c.meta.get("project"):
                    v = {"won": 2, "replied": 1}.get(c.meta.get("outcome") or "", 0)
                    counts[c.meta["project"]][0] += v
                    counts[c.meta["project"]][1] += 1
            self._prior = {p: (s + 1) / (2 * n + 2) for p, (s, n) in counts.items()}
        return self._prior

    def _apply_outcome(self, cands: list[tuple]) -> list[tuple]:
        if not self.cfg.outcome or not cands:
            return cands
        prior = self.project_prior()
        base = sum(prior.values()) / len(prior) if prior else 0.5
        scores = [s for _, s in cands]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        out = []
        for c, s in cands:
            p = prior.get(c.meta.get("project") or "", base)
            out.append((c, (s - lo) / span + self.cfg.outcome_weight * (p - base)))
        return sorted(out, key=lambda x: -x[1])

    def exemplars(self, job: dict, n: int = 2) -> list[dict]:
        """Winning phrasing: past proposals that won or got replies, most similar to this job (BM25)."""
        if not self.cfg.outcome:
            return []
        q = job.get("title", "") + " " + " ".join(job.get("skills", []))
        hits = self.index.bm25(q, 40, {"type": "proposal"})
        out = [c for c, _ in hits if c.meta.get("outcome") in ("won", "replied")][:n]
        return [{"doc_id": c.doc_id, "outcome": c.meta.get("outcome"), "text": c.text[:700]} for c in out]

    def _to_evidence(self, ranked: list[tuple], k: int) -> list[Evidence]:
        out = []
        for c, s in ranked[:k]:
            out.append(Evidence(doc_id=eval_doc_id(c.doc_id), chunk_id=c.chunk_id, type=c.meta.get("type", ""),
                                project=c.meta.get("project"), title=c.meta.get("title", ""), text=c.text,
                                score=round(float(s), 5), meta={kk: c.meta.get(kk) for kk in
                                                                ("date", "client_industry", "skills", "source")}))
        return out

    def _one(self, query: str, k: int, filters: dict | None) -> list[tuple]:
        cands = self._search(query, self.cfg.k_candidates, filters)
        cands = self._rerank(query, cands)
        return self._apply_outcome(cands)[:max(k, 10)]

    # ------------------------------------------------------------------ public API
    def queries(self, job: dict, requirements: list[str] | None) -> list[str]:
        if self.cfg.query == "decomposed":
            return requirements or heuristic_requirements(job)
        return [f"{job.get('title', '')}\n{job.get('description', '')[:2000]}"]

    def retrieve(self, job: dict, requirements: list[str] | None = None, k: int = 10,
                 filters: dict | None = None) -> RetrievalResult:
        t0 = time.perf_counter()
        if self.cfg.corrective:
            from rag.variants.v8_corrective import corrective_retrieve

            res = corrective_retrieve(self, job, requirements, k, filters)
        else:
            qs = self.queries(job, requirements)
            per_req: dict[str, list[Evidence]] = {}
            ranked_lists = []
            for q in qs:
                ranked = self._one(q, k, filters)
                ranked_lists.append(ranked)
                per_req[q] = self._to_evidence(ranked, 5)
            if len(qs) == 1:
                merged = ranked_lists[0]
            else:
                fused = rrf([[(c.chunk_id, s) for c, s in r] for r in ranked_lists])
                lookup = {c.chunk_id: c for r in ranked_lists for c, _ in r}
                merged = sorted(((lookup[i], s) for i, s in fused.items()), key=lambda x: -x[1])
            res = RetrievalResult(items=self._to_evidence(merged, k),
                                  per_requirement=per_req if self.cfg.query == "decomposed" else {})
        res.exemplars = self.exemplars(job)
        res.trace.update(variant=self.cfg.name, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                         n_queries=len(res.per_requirement) or 1)
        return res


def ndcg(ranked: list[str], grades: dict[str, int], k: int = 10) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def tokens_overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return len(ta & tb) / (len(ta) or 1)
