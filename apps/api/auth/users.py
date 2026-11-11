"""Users, password hashing (bcrypt) and session tokens (JWT in an httpOnly cookie, or a Bearer header)."""

from __future__ import annotations

import json
import os
import time
import uuid

import bcrypt
import jwt
from fastapi import Cookie, Depends, Header, HTTPException

from apps.api import db

COOKIE = "gb_session"
TTL_S = 7 * 86400
PERSONAS = {"ai_ml", "fullstack", "data_analyst"}


def _secret() -> str:
    s = os.getenv("APP_SECRET_KEY")
    if not s:
        raise RuntimeError("APP_SECRET_KEY is not set")
    return s


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=11)).decode()


def check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def issue(user_id: str) -> str:
    return jwt.encode({"sub": user_id, "exp": int(time.time()) + TTL_S, "iat": int(time.time())}, _secret(), "HS256")


def decode(token: str) -> str | None:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None


async def create_user(email: str, password: str, display_name: str, persona: str | None) -> dict:
    uid = "u-" + uuid.uuid4().hex[:12]
    corpus = persona if persona in PERSONAS else f"user-{uid}"
    await db.execute("INSERT INTO users (user_id, email, password_hash, display_name, corpus) VALUES (%s,%s,%s,%s,%s)",
                     uid, email.lower().strip(), hash_password(password), display_name, corpus)
    await db.execute("INSERT INTO user_settings (user_id, settings) VALUES (%s,%s)", uid, db.J({}))
    return await get_user(uid)


async def get_user(user_id: str) -> dict | None:
    return await db.fetchone("SELECT u.user_id, u.email, u.display_name, u.corpus, u.onboarded, s.settings "
                             "FROM users u JOIN user_settings s USING (user_id) WHERE u.user_id=%s", user_id)


async def current_user(gb_session: str | None = Cookie(default=None),
                       authorization: str | None = Header(default=None)) -> dict:
    token = gb_session or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
    uid = decode(token) if token else None
    user = await get_user(uid) if uid else None
    if not user:
        raise HTTPException(401, "not signed in")
    return user


CurrentUser = Depends(current_user)


def profile_of(user: dict) -> dict:
    s = user.get("settings") or {}
    p = dict(s.get("profile") or {})
    if user["corpus"] in PERSONAS and not p:
        from agent.settings import ROOT

        p = json.loads((ROOT / f"data/personas/{user['corpus']}/profile.json").read_text())
    p.setdefault("domain", user["corpus"] if user["corpus"] in PERSONAS else s.get("domain", "fullstack"))
    return p
