"""Claim verification (B3): split a draft into claims, check each against retrieved evidence, then remove or rewrite
unsupported claims BEFORE the Critic sees the draft. Reports the unsupported-claim count before and after.

Cheap first, LLM second:
  1. split into sentences; keep sentences that assert experience/results/tools (first-person past, numbers,
     citations, named skills); questions, plans ("I would...") and pleasantries are not claims
  2. deterministic check against the cited evidence: every number and every named skill in the claim must appear
     in that evidence, and content-word overlap must clear `claim_support_min`
  3. only claims the deterministic check cannot settle go to the verifier LLM (one batched call) for
     supported / partly_supported / unsupported + a grounded rewrite
  4. unsupported -> grounded rewrite if one passes step 2, else the sentence is removed;
     partly_supported -> rewrite when available, otherwise kept and flagged for the Critic
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent.handoffs import Claim, VerificationReport
from rag.skills import canon, canon_set, mentions
from rag.vocab import VOCAB

CITE = re.compile(r"\[((?:case|repo|review|cv|proposal|pdf):[^\]\s]+)\]")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s?(?:%|x|k|m|h|hours?|minutes?|days?|weeks?|months?|years?)?", re.I)
CLAIM_CUE = re.compile(r"\b(i|i've|i have|my|we)\b.{0,40}\b(built|led|delivered|shipped|created|developed|designed|"
                       r"implemented|reduced|cut|raised|increased|improved|automated|migrated|trained|deployed|"
                       r"worked|have|has|helped|handled|wrote|ran|set up|made|took|used|achieved)\b", re.I)
NON_CLAIM = re.compile(r"^(i would|i'd|i will|i can|i'll|could you|can you|would you|what|how|when|do you|are you|"
                       r"is the|happy to|looking forward|thanks|thank you|best|regards)\b", re.I)
STOP = set("the a an and or of to in on for with at by from my our your this that it is was were be been as into "
           "over under than then so we i you they he she their its which who what when where how".split())


def content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.\-]{2,}", s.lower()) if w not in STOP}


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        for s in SENT.split(para.strip()):
            if s.strip():
                out.append(s.strip())
    return out


def is_claim(sentence: str) -> bool:
    s = sentence.strip()
    if s.endswith("?") or NON_CLAIM.search(s):
        return False
    if CITE.search(s) or CLAIM_CUE.search(s):
        return True
    has_num = bool(re.search(r"\d", s))
    has_skill = bool(mentions(s, VOCAB))
    return has_num and has_skill


def numbers(s: str) -> set[str]:
    return {re.sub(r"[,\s$]", "", m.group(0)).lower().rstrip("hoursminutesdaysweeksmonthsyear")
            for m in NUM.finditer(CITE.sub("", s)) if re.search(r"\d", m.group(0))}


def deterministic_check(claim: str, evidence: list[dict], min_overlap: float) -> tuple[str, list[str], str]:
    """Returns (status, evidence_ids, note). status in supported | uncertain | unsupported."""
    cited = CITE.findall(claim)
    body = CITE.sub("", claim)
    pool = [e for e in evidence if e["doc_id"] in cited] if cited else evidence
    if cited and not pool:
        return "unsupported", cited, "cites evidence that was not retrieved for this job"
    if not pool:
        return "unsupported", [], "no evidence available"
    nums = numbers(body)
    skills = {canon(s) for s in mentions(body, VOCAB)}
    cw = content_words(body)
    best: tuple[float, dict | None] = (0.0, None)
    for e in pool:
        text = (e.get("text") or e.get("snippet") or "").lower()
        e_skills = canon_set(e.get("skills") or []) | {canon(s) for s in mentions(text, VOCAB)}
        num_ok = all(n.rstrip("%x") in re.sub(r"[,\s$]", "", text) for n in nums)
        skill_ok = skills <= e_skills
        overlap = len(cw & content_words(text)) / (len(cw) or 1)
        score = overlap + (0.3 if num_ok and nums else 0) + (0.2 if skill_ok and skills else 0)
        if num_ok and skill_ok and overlap >= min_overlap:
            return "supported", [e["doc_id"]], f"overlap {overlap:.2f}"
        if score > best[0]:
            best = (score, e)
    if best[1] is not None and best[0] >= min_overlap * 0.6:
        return "uncertain", [best[1]["doc_id"]], f"partial overlap {best[0]:.2f}"
    return "unsupported", cited, "evidence does not mention this"


class _LLMResult(BaseModel):
    id: int
    status: str
    rewrite: str = ""
    note: str = ""


class _LLMOut(BaseModel):
    results: list[_LLMResult] = Field(default_factory=list)


async def verify_text(text: str, evidence: list[dict], min_overlap: float = 0.45,
                      use_llm: bool = True) -> tuple[str, list[Claim], VerificationReport]:
    """Verify one text (cover letter or answer). Returns (cleaned_text, claims, report)."""
    sentences = split_sentences(text)
    indexed: list[tuple[int, Claim]] = []  # (sentence index, claim)
    uncertain: list[tuple[int, Claim]] = []
    for i, s in enumerate(sentences):
        if not is_claim(s):
            continue
        status, ids, note = deterministic_check(s, evidence, min_overlap)
        c = Claim(text=s, evidence_ids=ids, status="supported" if status == "supported" else "unsupported", note=note)
        indexed.append((i, c))
        if status == "uncertain" or (status == "unsupported" and use_llm):
            uncertain.append((i, c))
    llm_checked = 0
    rewrites: dict[int, str] = {}
    if uncertain and use_llm:
        from agent.llm import LLMError, chat, load_prompt

        ev_text = "\n".join(f"[{e['doc_id']}] {(e.get('text') or e.get('snippet') or '')[:500]}"
                            for e in _dedupe(evidence)[:12])
        claims_txt = "\n".join(f"{n}. {c.text}" for n, (_, c) in enumerate(uncertain))
        try:
            res = await chat("verifier", [{"role": "system", "content": load_prompt("verifier")},
                                          {"role": "user", "content": f"EVIDENCE\n{ev_text}\n\nCLAIMS\n{claims_txt}"}],
                             schema=_LLMOut, max_tokens=1200)
            llm_checked = len(uncertain)
            for r in res.parsed.results:
                if 0 <= r.id < len(uncertain):
                    idx, c = uncertain[r.id]
                    if r.status in ("supported", "partly_supported", "unsupported"):
                        c.status = r.status  # type: ignore[assignment]
                    c.note = (r.note or c.note)[:120]
                    if r.rewrite and r.status != "supported":
                        rewrites[idx] = r.rewrite
        except LLMError:
            pass  # deterministic verdicts stand; uncertain ones count as unsupported
    claims = [c for _, c in indexed]
    unsupported_before = sum(c.status == "unsupported" for c in claims)
    out_sentences = list(sentences)
    for idx, c in indexed:
        if c.status == "supported":
            continue
        rw = rewrites.get(idx)
        if rw:
            st, ids, _ = deterministic_check(rw, evidence, min_overlap * 0.8)
            if st != "unsupported" and not CITE.search(rw) and (ids or c.evidence_ids):
                rw = re.sub(r"([.!?])?\s*$", lambda m: f" [{(ids or c.evidence_ids)[0]}]" + (m.group(1) or "."), rw, count=1)
            if st != "unsupported":
                out_sentences[idx] = rw
                c.action = "rewritten"
                c.evidence_ids = ids or c.evidence_ids
                c.status = "supported" if st == "supported" else "partly_supported"
                continue
        if c.status == "unsupported":
            out_sentences[idx] = ""
            c.action = "removed"
    unsupported_after = sum(c.status == "unsupported" and c.action != "removed" for c in claims)
    cleaned = _rejoin(text, sentences, out_sentences)
    report = VerificationReport(claims=claims, unsupported_before=unsupported_before, unsupported_after=unsupported_after,
                                total_claims=len(claims), llm_checked=llm_checked)
    return cleaned, claims, report


def _dedupe(evidence: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in evidence:
        if e["doc_id"] not in seen:
            seen.add(e["doc_id"])
            out.append(e)
    return out


def _rejoin(original: str, sentences: list[str], new: list[str]) -> str:
    paras = [p for p in re.split(r"\n\s*\n", original.strip())]
    out_paras, k = [], 0
    for p in paras:
        n = len([s for s in SENT.split(p.strip()) if s.strip()])
        kept = [s for s in new[k:k + n] if s]
        k += n
        if kept:
            out_paras.append(" ".join(kept))
    return "\n\n".join(out_paras)


def render_for_client(text: str, titles: dict[str, str]) -> str:
    """Client-facing text: internal evidence ids become a readable portfolio reference (first mention only).
    The approval token is signed over THIS text, so what the human approves is exactly what is sent."""
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        doc = m.group(1)
        if doc in seen:
            return ""
        seen.add(doc)
        kind = {"case": "case study", "repo": "repository", "review": "client review", "cv": "CV"}.get(doc.split(":")[0], "portfolio")
        title = titles.get(doc) or doc.split(":", 1)[1]
        return f" (portfolio {kind}: {title})" if kind != "CV" else ""

    out = re.sub(r"\s*\[((?:case|repo|review|cv|proposal|pdf):[^\]\s]+)\]", repl, text)
    return re.sub(r"\s+([.,;!?])", r"\1", re.sub(r"[ \t]{2,}", " ", out)).strip()
