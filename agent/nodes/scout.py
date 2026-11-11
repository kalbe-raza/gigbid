"""Scout: run saved searches on a schedule, de-duplicate, queue new jobs (worker/), and hand one job into the
bid graph (`intake` node). Deterministic; no LLM.

Duplicate detection: word 3-shingles of the normalised title+description, Jaccard >= 0.8 against jobs already in
the user's inbox -> marked `duplicate_of` and skipped without spending any LLM tokens.
"""

from __future__ import annotations

import hashlib
import re

from agent.nodes.common import NodeContext, gigbid_node

DUP_THRESHOLD = 0.8
_PREFIX = re.compile(r"^(urgent|re-?posting|repost|asap|new|hiring)\s*[:!-]\s*", re.I)


def normalise(title: str, description: str) -> str:
    t = _PREFIX.sub("", (title or "").strip())
    text = f"{t} {description or ''}".lower()
    text = re.sub(r"\(re-?posted[^)]*\)", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def shingles(text: str, n: int = 3) -> set[str]:
    w = text.split()
    return {" ".join(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


def fingerprint(title: str, description: str) -> str:
    """Stable 64-bit simhash (hex) used to index candidates; exact Jaccard confirms."""
    v = [0] * 64
    for s in shingles(normalise(title, description)):
        h = int(hashlib.md5(s.encode()).hexdigest()[:16], 16)
        for i in range(64):
            v[i] += 1 if h >> i & 1 else -1
    return f"{sum(1 << i for i in range(64) if v[i] > 0):016x}"


def jaccard(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    return len(sa & sb) / (len(sa | sb) or 1)


def find_duplicate(job: dict, seen: list[dict]) -> str | None:
    """`seen`: [{job_id, title, description}] already queued for this user. Returns the original's id."""
    me = normalise(job.get("title", ""), job.get("description", ""))
    for s in seen:
        if s["job_id"] == job.get("job_id"):
            continue
        if jaccard(me, normalise(s.get("title", ""), s.get("description", ""))) >= DUP_THRESHOLD:
            return s["job_id"]
    return None


@gigbid_node("intake")
async def intake(state: dict, ctx: NodeContext) -> dict:
    """Scout hands a job in: make sure we have the full post (description + Connects under current policy)."""
    job = dict(state.get("job") or {})
    if not job.get("description") or "connects" not in job:
        full = await ctx.tools("intake").call("marketplace", "get_job", {"job_id": job["job_id"]})
        job.update(full)
    return {"job": job, "status": "analysing", "revision": 0,
            "_summary": f"loaded {job.get('title', '')[:60]!r}"}
