"""Postgres access and schema for the API, worker and MCP servers.

Every query that touches user data takes `user_id` and filters on it (tenant scoping, E4).
The audit_log table is append-only: a trigger rejects UPDATE and DELETE.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT,
  corpus TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), onboarded BOOLEAN DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT PRIMARY KEY REFERENCES users ON DELETE CASCADE, settings JSONB NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS credentials (
  user_id TEXT REFERENCES users ON DELETE CASCADE, provider TEXT, ciphertext BYTEA NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (user_id, provider));
CREATE TABLE IF NOT EXISTS saved_searches (
  search_id SERIAL PRIMARY KEY, user_id TEXT REFERENCES users ON DELETE CASCADE, query TEXT, domain TEXT,
  interval_s INTEGER NOT NULL DEFAULT 900, last_run TIMESTAMPTZ, enabled BOOLEAN DEFAULT TRUE);
CREATE TABLE IF NOT EXISTS inbox (
  user_id TEXT REFERENCES users ON DELETE CASCADE, job_id TEXT, fingerprint TEXT, job JSONB, status TEXT,
  thread_id TEXT, summary JSONB DEFAULT '{}', duplicate_of TEXT, created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (user_id, job_id));
CREATE INDEX IF NOT EXISTS inbox_fp ON inbox (user_id, fingerprint);
CREATE TABLE IF NOT EXISTS runs (
  thread_id TEXT PRIMARY KEY, user_id TEXT REFERENCES users ON DELETE CASCADE, kind TEXT, ref TEXT,
  status TEXT, trace_id TEXT, error TEXT, attempts INTEGER DEFAULT 0, usage JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS run_events (
  id BIGSERIAL PRIMARY KEY, thread_id TEXT, user_id TEXT, node TEXT, kind TEXT, payload JSONB,
  created_at TIMESTAMPTZ DEFAULT now());
ALTER TABLE runs ADD COLUMN IF NOT EXISTS pending_resume JSONB;
CREATE INDEX IF NOT EXISTS run_events_thread ON run_events (thread_id, id);
CREATE TABLE IF NOT EXISTS pipeline (
  user_id TEXT REFERENCES users ON DELETE CASCADE, job_id TEXT, proposal_id TEXT, thread_id TEXT, title TEXT,
  stage TEXT, category TEXT, connects INTEGER DEFAULT 0, amount NUMERIC, features JSONB DEFAULT '{}',
  marketplace_thread_id TEXT, updated_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (user_id, job_id));
CREATE TABLE IF NOT EXISTS conversations (
  user_id TEXT REFERENCES users ON DELETE CASCADE, marketplace_thread_id TEXT, graph_thread_id TEXT,
  job_id TEXT, status TEXT, last_seen_message TEXT, updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, marketplace_thread_id));
CREATE TABLE IF NOT EXISTS approvals_log (
  id BIGSERIAL PRIMARY KEY, user_id TEXT, thread_id TEXT, kind TEXT, decision TEXT, reason TEXT,
  diff JSONB, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS portfolio_uploads (
  id BIGSERIAL PRIMARY KEY, user_id TEXT REFERENCES users ON DELETE CASCADE, filename TEXT, doc_type TEXT,
  doc_ids JSONB, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS workspace_holds (
  hold_id TEXT PRIMARY KEY, user_id TEXT, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, title TEXT,
  status TEXT DEFAULT 'tentative', created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS workspace_drafts (
  draft_id TEXT PRIMARY KEY, user_id TEXT, to_addr TEXT, subject TEXT, body TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS mcp_drafts (
  user_id TEXT, proposal_id TEXT, content_hash TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, proposal_id));
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ DEFAULT now(), server TEXT NOT NULL, user_id TEXT, tool TEXT NOT NULL,
  arguments JSONB, result JSONB, trace_id TEXT, ok BOOLEAN);
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""

_pool: AsyncConnectionPool | None = None


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "postgresql://gigbid:gigbid@localhost:55432/gigbid")
    return url.replace("postgresql+psycopg://", "postgresql://")


async def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(database_url(), min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "8")),
                                    kwargs={"autocommit": True, "row_factory": dict_row}, open=False)
        await _pool.open()
    return _pool


async def init_schema() -> None:
    p = await pool()
    async with p.connection() as c:
        await c.execute("SELECT pg_advisory_lock(794001)")
        try:
            await c.execute(SCHEMA)
        finally:
            await c.execute("SELECT pg_advisory_unlock(794001)")


@asynccontextmanager
async def conn():
    p = await pool()
    async with p.connection() as c:
        yield c


async def fetch(sql: str, *args) -> list[dict[str, Any]]:
    async with conn() as c:
        cur = await c.execute(sql, args)
        return await cur.fetchall()


async def fetchone(sql: str, *args) -> dict[str, Any] | None:
    async with conn() as c:
        cur = await c.execute(sql, args)
        return await cur.fetchone()


async def execute(sql: str, *args) -> None:
    async with conn() as c:
        await c.execute(sql, args)


def J(v: Any) -> Jsonb:
    return Jsonb(json.loads(json.dumps(v, default=str)))


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
