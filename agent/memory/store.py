"""Long-term memory on a LangGraph store (Postgres in production, in-memory in tests).

Namespaces per user (tenant-scoped):
  ("gigbid", user, "style_candidates")   abstract edit patterns + counts (staging; not used by agents)
  ("gigbid", user, "style")              PROMOTED style rules the Writer must follow
  ("gigbid", user, "outcome_stats")      per-category reply counts by feature bucket (staging)
  ("gigbid", user, "outcome")            PROMOTED outcome insights the Writer sees for that category
Only agent/memory/promotion_rules.py decides what moves from staging to promoted.
"""

from __future__ import annotations

import os
import time

from langgraph.store.base import BaseStore

from agent.memory import promotion_rules as rules

_store: BaseStore | None = None


async def get_store() -> BaseStore:
    global _store
    if _store is None:
        if os.getenv("DATABASE_URL"):
            from langgraph.store.postgres.aio import AsyncPostgresStore
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            from apps.api.db import database_url

            conn = await AsyncConnection.connect(database_url(), autocommit=True, prepare_threshold=0,
                                                 row_factory=dict_row)
            store = AsyncPostgresStore(conn)
            await store.setup()
            _store = store
        else:
            from langgraph.store.memory import InMemoryStore

            _store = InMemoryStore()
    return _store


def set_store(store: BaseStore) -> None:
    global _store
    _store = store


def ns(user: str, kind: str) -> tuple[str, ...]:
    return ("gigbid", user, kind)


async def observe_edit(user: str, job_id: str, original: str, edited: str) -> list[dict]:
    """Record an approved edit; returns any rules promoted by this observation."""
    store = await get_store()
    promoted = []
    for pat, detail in rules.edit_patterns(original, edited).items():
        item = await store.aget(ns(user, "style_candidates"), pat)
        cand = dict(item.value) if item else {"pattern": pat, "count": 0, "jobs": [], "words": []}
        cand["count"] += 1
        cand["jobs"] = sorted(set(cand["jobs"]) | {job_id})
        if "words" in detail:
            cand["words"] = (cand.get("words") or [])[-9:] + [detail["words"]]
        cand["last_seen"] = time.time()
        await store.aput(ns(user, "style_candidates"), pat, cand)
        existing = await store.aget(ns(user, "style"), pat)
        if not existing and rules.should_promote_style(cand):
            rule = {"pattern": pat, "rule": rules.style_rule_text(pat, cand), "evidence_count": cand["count"],
                    "jobs": cand["jobs"], "promoted_at": time.time(),
                    "promotion_rule": "style: >= min_occurrences edits on >= min_distinct_jobs jobs"}
            await store.aput(ns(user, "style"), pat, rule)
            promoted.append(rule)
    return promoted


async def style_rules(user: str) -> list[dict]:
    store = await get_store()
    items = await store.asearch(ns(user, "style"), limit=50)
    max_rules = 20
    return [dict(i.value) | {"key": i.key} for i in items][:max_rules]


async def observe_outcome(user: str, category: str, features: dict, replied: bool) -> list[dict]:
    store = await get_store()
    item = await store.aget(ns(user, "outcome_stats"), category)
    st = dict(item.value) if item else {"n": 0, "replied": 0, "buckets": {}}
    st["n"] += 1
    st["replied"] += int(replied)
    for f, v in features.items():
        b = st["buckets"].setdefault(f"{f}={v}", {"n": 0, "replied": 0})
        b["n"] += 1
        b["replied"] += int(replied)
    await store.aput(ns(user, "outcome_stats"), category, st)
    promoted = []
    for ins in rules.outcome_insights(st, category):
        await store.aput(ns(user, "outcome"), ins["key"], ins | {"promoted_at": time.time(),
                                                                 "promotion_rule": "outcome: n >= min_samples and "
                                                                                   "rate - base >= min_rate_delta"})
        promoted.append(ins)
    return promoted


async def outcome_insights(user: str, category: str | None = None) -> list[dict]:
    store = await get_store()
    items = await store.asearch(ns(user, "outcome"), limit=100)
    out = [dict(i.value) | {"key": i.key} for i in items]
    return [o for o in out if category is None or o.get("category") == category]


async def list_memory(user: str) -> dict:
    return {"style": await style_rules(user), "outcome": await outcome_insights(user)}


async def delete_memory(user: str, kind: str, key: str) -> bool:
    if kind not in ("style", "outcome"):
        return False
    store = await get_store()
    if not await store.aget(ns(user, kind), key):
        return False
    await store.adelete(ns(user, kind), key)
    if kind == "style":  # forget the evidence too, so it is not re-promoted by the next single edit
        await store.adelete(ns(user, "style_candidates"), key)
    return True
