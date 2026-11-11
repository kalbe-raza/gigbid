"""OAuth 2.1 for the official Upwork MCP server (A4): authorization-code + PKCE, metadata discovery, encrypted token
storage per user, refresh before expiry. Tokens never appear in logs or traces.

Discovery follows the MCP authorization spec: GET <mcp origin>/.well-known/oauth-protected-resource ->
authorization server -> /.well-known/oauth-authorization-server (endpoints). A client id comes from
UPWORK_CLIENT_ID, or from dynamic client registration when the server supports it.
NOTE: exercised against tests/integration/mock_upwork_mcp.py only (no Upwork account was available).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx

from agent.settings import settings
from apps.api.auth import token_store

_PENDING: dict[str, dict] = {}  # state -> {verifier, user_id, created}; short-lived, in memory by design


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


async def discover(mcp_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        pr = await c.get(_origin(mcp_url) + "/.well-known/oauth-protected-resource")
        pr.raise_for_status()
        issuer = pr.json()["authorization_servers"][0]
        meta = await c.get(issuer.rstrip("/") + "/.well-known/oauth-authorization-server")
        meta.raise_for_status()
        return meta.json()


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _client_id(meta: dict, redirect_uri: str) -> str:
    if os.getenv("UPWORK_CLIENT_ID"):
        return os.environ["UPWORK_CLIENT_ID"]
    if not meta.get("registration_endpoint"):
        raise RuntimeError("Set UPWORK_CLIENT_ID: the authorization server does not support dynamic registration")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(meta["registration_endpoint"], json={
            "client_name": "GigBid", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none"})
        r.raise_for_status()
        return r.json()["client_id"]


async def start(user_id: str, redirect_uri: str) -> str:
    mcp_url = settings().marketplace["upwork_live"]["mcp_url"]
    meta = await discover(mcp_url)
    cid = await _client_id(meta, redirect_uri)
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {"verifier": verifier, "user_id": user_id, "created": time.time(), "client_id": cid,
                       "token_endpoint": meta["token_endpoint"], "redirect_uri": redirect_uri}
    return meta["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": mcp_url})


async def finish(state: str, code: str) -> str:
    p = _PENDING.pop(state, None)
    if not p or time.time() - p["created"] > 600:
        raise RuntimeError("OAuth state expired or unknown")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(p["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect_uri"],
            "client_id": p["client_id"], "code_verifier": p["verifier"]})
        r.raise_for_status()
        tok = r.json()
    await token_store.put(p["user_id"], "upwork_live", {
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)), "client_id": p["client_id"],
        "token_endpoint": p["token_endpoint"]})
    return p["user_id"]


async def ensure_fresh(user_id: str, creds: dict) -> dict:
    if creds.get("expires_at", 0) - time.time() > 120 or not creds.get("refresh_token"):
        return creds
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(creds["token_endpoint"], data={"grant_type": "refresh_token",
                                                         "refresh_token": creds["refresh_token"],
                                                         "client_id": creds["client_id"]})
        r.raise_for_status()
        tok = r.json()
    creds = creds | {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", creds["refresh_token"]),
                     "expires_at": time.time() + int(tok.get("expires_in", 3600))}
    await token_store.put(user_id, "upwork_live", creds)
    return creds
