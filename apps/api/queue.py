"""Enqueue background work on the Arq worker (Redis). Long agent runs never execute inside a web request."""

from __future__ import annotations

import os

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


async def pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue(fn: str, *args, job_id: str | None = None, defer_s: float | None = None) -> bool:
    """Returns False when an identical job (same job_id) is already queued or running: idempotent enqueue."""
    p = await pool()
    job = await p.enqueue_job(fn, *args, _job_id=job_id, _defer_by=defer_s)
    return job is not None


async def degraded_flag() -> dict | None:
    p = await pool()
    raw = await p.get("gigbid:degraded:marketplace")
    if not raw:
        return None
    return {"since": float(raw)}


async def queued_count() -> int:
    p = await pool()
    return int(await p.zcard("arq:queue"))
