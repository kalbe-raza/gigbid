"""Settings (autonomy, budgets, saved searches, connected accounts), the memory viewer, portfolio uploads, health."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile

from agent.mcp_client import server_health, server_urls
from agent.memory import store as memory
from agent.settings import ROOT, settings
from apps.api import db
from apps.api.auth import token_store
from apps.api.auth.users import PERSONAS, CurrentUser, profile_of
from apps.api.schemas.models import (
    Health,
    MemoryItem,
    Profile,
    SavedSearch,
    SettingsIn,
    SettingsOut,
    UploadOut,
)

router = APIRouter(prefix="/api", tags=["settings"])
UPLOADS = Path(os.getenv("UPLOADS_DIR", ROOT / "data" / "uploads"))


async def _out(user: dict) -> SettingsOut:
    s = user.get("settings") or {}
    searches = await db.fetch("SELECT search_id, query, domain, interval_s, enabled FROM saved_searches WHERE user_id=%s "
                              "ORDER BY search_id", user["user_id"])
    p = profile_of(user)
    return SettingsOut(profile=Profile(hourly_rate=p.get("hourly_rate", 30), hourly_floor=p.get("hourly_floor", 20),
                                       min_project=p.get("min_project", 100), domain=p.get("domain", "fullstack"),
                                       headline=p.get("headline")),
                       autonomy=s.get("autonomy", "manual"), weekly_connects_budget=s.get("weekly_connects_budget", 80),
                       saved_searches=[SavedSearch(**r) for r in searches],
                       connected_accounts=await token_store.providers(user["user_id"]),
                       marketplace_mode=settings().marketplace["mode"],
                       budgets=settings().budgets["declared"] | {"max_tokens_per_job": settings().budgets["max_tokens_per_job"],
                                                                 "max_graph_steps": settings().budgets["max_graph_steps"]})


@router.get("/settings", response_model=SettingsOut)
async def get_settings(user=CurrentUser):
    return await _out(user)


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsIn, user=CurrentUser):
    s = dict(user.get("settings") or {})
    if body.profile:
        s["profile"] = body.profile.model_dump()
    if body.autonomy:
        s["autonomy"] = body.autonomy
    if body.weekly_connects_budget is not None:
        s["weekly_connects_budget"] = body.weekly_connects_budget
    await db.execute("UPDATE user_settings SET settings=%s WHERE user_id=%s", db.J(s), user["user_id"])
    if body.saved_searches is not None:
        await db.execute("DELETE FROM saved_searches WHERE user_id=%s", user["user_id"])
        for q in body.saved_searches:
            await db.execute("INSERT INTO saved_searches (user_id, query, domain, interval_s, enabled) VALUES (%s,%s,%s,%s,%s)",
                             user["user_id"], q.query, q.domain, q.interval_s, q.enabled)
    from apps.api.auth.users import get_user

    return await _out(await get_user(user["user_id"]))


@router.get("/memory", response_model=list[MemoryItem])
async def get_memory(user=CurrentUser):
    m = await memory.list_memory(user["user_id"])
    out = [MemoryItem(kind="style", key=r["key"], text=r["rule"],
                      evidence={"edits": r.get("evidence_count"), "jobs": r.get("jobs"), "rule": r.get("promotion_rule")})
           for r in m["style"]]
    out += [MemoryItem(kind="outcome", key=r["key"], text=r["insight"],
                       evidence={"n": r.get("n"), "rate": r.get("rate"), "base_rate": r.get("base_rate"),
                                 "rule": r.get("promotion_rule")}) for r in m["outcome"]]
    return out


@router.delete("/memory/{kind}/{key:path}")
async def delete_memory(kind: str, key: str, user=CurrentUser) -> dict:
    if not await memory.delete_memory(user["user_id"], kind, key):
        raise HTTPException(404, "memory item not found")
    return {"deleted": True}


ALLOWED = {".pdf": "cv", ".md": "case_study", ".zip": "repo", ".jsonl": "records"}


@router.post("/portfolio/upload", response_model=UploadOut)
async def upload(file: UploadFile = File(...), user=CurrentUser):
    """Add work to the user's own portfolio and re-index incrementally (persona corpora are read-only)."""
    if user["corpus"] in PERSONAS:
        raise HTTPException(400, "demo personas are read-only; register with 'own profile' to upload")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"unsupported file type {ext}; use PDF CV, Markdown case study, repo .zip or .jsonl")
    data = await file.read()
    if len(data) > 5_000_000:
        raise HTTPException(413, "file too large (5 MB max)")
    root = UPLOADS / user["user_id"]
    if ext == ".pdf":
        (root).mkdir(parents=True, exist_ok=True)
        (root / "cv.pdf").write_bytes(data)
    elif ext == ".md":
        (root / "case_studies").mkdir(parents=True, exist_ok=True)
        (root / "case_studies" / name).write_bytes(data)
    elif ext == ".zip":
        dest = root / "repos" / Path(name).stem
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            for m in z.infolist()[:300]:
                target = (dest / m.filename).resolve()
                if not str(target).startswith(str(dest.resolve())) or m.is_dir() or m.file_size > 200_000:
                    continue  # zip-slip and oversize protection
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(m))
    else:
        kind = "proposals" if "proposal" in name else "reviews"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{kind}.jsonl").write_bytes(data)
    base = server_urls()["portfolio"].rsplit("/mcp", 1)[0]
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(base + "/admin/reindex", json={"corpus": user["corpus"]},
                             headers={"Authorization": f"Bearer {os.getenv('MCP_SERVICE_TOKEN', '')}"})
            r.raise_for_status()
            info = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(503, "saved, but the portfolio server is unavailable; it will index on next start") from e
    await db.execute("INSERT INTO portfolio_uploads (user_id, filename, doc_type, doc_ids) VALUES (%s,%s,%s,%s)",
                     user["user_id"], name, ALLOWED[ext], db.J([]))
    return UploadOut(filename=name, doc_type=ALLOWED[ext], documents=info.get("documents", 0),
                     chunks=info.get("chunks_upserted"))


@router.get("/health", response_model=Health)
async def health():
    h = await server_health()
    queued, flag = 0, None
    try:
        from apps.api.queue import degraded_flag, queued_count

        queued, flag = await queued_count(), await degraded_flag()
    except Exception:  # noqa: BLE001 - Redis down is itself a degraded state
        flag = {"since": 0}
    marketplace_ok = h.get("marketplace", False) and not flag
    degraded = not all(h.values()) or flag is not None
    msg = None
    if not marketplace_ok:
        msg = "Marketplace unavailable - new work is queued and will resume automatically."
    elif degraded:
        msg = "Some services are degraded: " + ", ".join(k for k, v in h.items() if not v)
    return Health(api=True, marketplace=marketplace_ok, portfolio=h.get("portfolio", False),
                  workspace=h.get("workspace", False), degraded=degraded, message=msg, queued_jobs=queued)
