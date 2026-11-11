"""Sign-in, registration, onboarding (persona or own profile, marketplace connection, rates, filters, Connects budget)
and the Upwork OAuth redirect flow."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from apps.api import db
from apps.api.auth import oauth_upwork, token_store
from apps.api.auth.users import (
    COOKIE,
    PERSONAS,
    TTL_S,
    CurrentUser,
    check_password,
    create_user,
    get_user,
    issue,
)
from apps.api.queue import enqueue
from apps.api.schemas.models import LoginIn, OnboardingIn, RegisterIn, SavedSearch, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _out(u: dict) -> User:
    return User(user_id=u["user_id"], email=u["email"], display_name=u.get("display_name"), corpus=u["corpus"],
                onboarded=u["onboarded"], persona=u["corpus"] if u["corpus"] in PERSONAS else "own")


def _set_cookie(resp: Response, user_id: str) -> None:
    resp.set_cookie(COOKIE, issue(user_id), max_age=TTL_S, httponly=True, samesite="lax",
                    secure=os.getenv("COOKIE_SECURE", "0") == "1", path="/")


@router.post("/register", response_model=User)
async def register(body: RegisterIn, resp: Response):
    if await db.fetchone("SELECT 1 FROM users WHERE email=%s", body.email.lower().strip()):
        raise HTTPException(409, "email already registered")
    u = await create_user(body.email, body.password, body.display_name, None if body.persona == "own" else body.persona)
    _set_cookie(resp, u["user_id"])
    return _out(u)


@router.post("/login", response_model=User)
async def login(body: LoginIn, resp: Response):
    row = await db.fetchone("SELECT user_id, password_hash FROM users WHERE email=%s", body.email.lower().strip())
    if not row or not check_password(body.password, row["password_hash"]):
        raise HTTPException(401, "wrong email or password")
    _set_cookie(resp, row["user_id"])
    return _out(await get_user(row["user_id"]))


@router.post("/logout")
async def logout(resp: Response) -> dict:
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=User)
async def me(user=CurrentUser):
    return _out(user)


@router.post("/onboarding", response_model=User)
async def onboarding(body: OnboardingIn, user=CurrentUser):
    uid = user["user_id"]
    if body.marketplace == "gigsim":
        token = body.gigsim_token or os.getenv("GIGSIM_DEMO_TOKEN") or os.getenv("GIGSIM_API_TOKEN")
        if not token:
            raise HTTPException(400, "no GigSim token")
        await token_store.put(uid, "gigsim", {"token": token})
    settings = dict(user.get("settings") or {})
    settings.update(profile=body.profile.model_dump(), weekly_connects_budget=body.weekly_connects_budget,
                    marketplace=body.marketplace, autonomy=settings.get("autonomy", "manual"))
    await db.execute("UPDATE user_settings SET settings=%s WHERE user_id=%s", db.J(settings), uid)
    await db.execute("DELETE FROM saved_searches WHERE user_id=%s", uid)
    searches = body.saved_searches or [SavedSearch(query="", domain=body.profile.domain)]
    for s in searches:
        await db.execute("INSERT INTO saved_searches (user_id, query, domain, interval_s, enabled) VALUES (%s,%s,%s,%s,%s)",
                         uid, s.query, s.domain, s.interval_s, s.enabled)
    await db.execute("UPDATE users SET onboarded=TRUE WHERE user_id=%s", uid)
    await enqueue("scout_scan", uid, job_id=f"scan:{uid}:onboarding")
    return _out(await get_user(uid))


@router.get("/upwork/start")
async def upwork_start(request: Request, user=CurrentUser):
    redirect = str(request.url_for("upwork_callback"))
    try:
        url = await oauth_upwork.start(user["user_id"], redirect)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Upwork OAuth unavailable: {type(e).__name__}: {str(e)[:200]}") from e
    return {"authorize_url": url}


@router.get("/upwork/callback", name="upwork_callback")
async def upwork_callback(state: str, code: str):
    try:
        await oauth_upwork.finish(state, code)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"OAuth failed: {type(e).__name__}") from e
    return RedirectResponse("/settings?connected=upwork")
