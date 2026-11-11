"""Approval tokens: defence in depth for R4.

When the user clicks Approve in the UI, the API signs the exact content being approved. The Marketplace MCP
server refuses any non-dry-run write whose content does not match a valid signature, so no agent (including
the single-agent baseline) can send something a human did not approve, even if a prompt is subverted.
The signing key lives only in the API and the Marketplace MCP server, never in the worker or agents.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time


def content_hash(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


def _key() -> bytes:
    k = os.getenv("APPROVAL_SIGNING_KEY")
    if not k:
        raise RuntimeError("APPROVAL_SIGNING_KEY is not set")
    return k.encode()


def sign(user_id: str, kind: str, chash: str, ttl_s: int = 7 * 86400) -> str:
    exp = int(time.time()) + ttl_s
    msg = f"{user_id}|{kind}|{chash}|{exp}".encode()
    return f"{exp}.{hmac.new(_key(), msg, hashlib.sha256).hexdigest()}"


def verify(token: str | None, user_id: str, kind: str, chash: str) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    msg = f"{user_id}|{kind}|{chash}|{exp}".encode()
    return hmac.compare_digest(sig, hmac.new(_key(), msg, hashlib.sha256).hexdigest())


def proposal_hash(job_id: str, cover_letter: str, bid_amount: float, bid_type: str) -> str:
    return content_hash("proposal", job_id, cover_letter.strip(), f"{float(bid_amount):.2f}", bid_type)


def message_hash(thread_id: str, text: str) -> str:
    return content_hash("message", thread_id, text.strip())
