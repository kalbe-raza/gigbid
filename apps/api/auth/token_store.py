"""Encrypted per-user credential store (marketplace tokens, OAuth tokens). E4: tokens are encrypted at rest
with Fernet (AES-128-CBC + HMAC), keyed from TOKEN_ENCRYPTION_KEY, scoped by user_id, and never logged."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from apps.api import db


def _fernet() -> Fernet:
    secret = os.getenv("TOKEN_ENCRYPTION_KEY") or os.getenv("APP_SECRET_KEY")
    if not secret:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(payload: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(payload).encode())


def decrypt(blob: bytes) -> dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(bytes(blob)))
    except InvalidToken as e:
        raise RuntimeError("credential could not be decrypted (wrong TOKEN_ENCRYPTION_KEY?)") from e


async def put(user_id: str, provider: str, payload: dict[str, Any]) -> None:
    await db.execute("INSERT INTO credentials (user_id, provider, ciphertext) VALUES (%s,%s,%s) "
                     "ON CONFLICT (user_id, provider) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, updated_at=now()",
                     user_id, provider, encrypt(payload))


async def get(user_id: str, provider: str) -> dict[str, Any] | None:
    row = await db.fetchone("SELECT ciphertext FROM credentials WHERE user_id=%s AND provider=%s", user_id, provider)
    return decrypt(row["ciphertext"]) if row else None


async def delete(user_id: str, provider: str) -> None:
    await db.execute("DELETE FROM credentials WHERE user_id=%s AND provider=%s", user_id, provider)


async def providers(user_id: str) -> list[str]:
    rows = await db.fetch("SELECT provider FROM credentials WHERE user_id=%s", user_id)
    return [r["provider"] for r in rows]
