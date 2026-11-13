"""GigSim: a small, repeatable stand-in for a freelance marketplace.

REST API with jobs, clients (with HIDDEN preferences), Connects, proposals, threads/messages, an event
feed, per-token rate limits and injected 5xx errors. Simulated clients decide whether to view, reply,
interview or hire, either with an LLM (GIGSIM_CLIENT_MODE=llm) or a seeded rule model (=rules).

Everything here is synthetic or derived from public Kaggle data; see docs/DATA_CARD.md.
Run:  uvicorn gigsim.app:app --port 8081
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
SEED = HERE / "seed"
DB_PATH = Path(os.getenv("GIGSIM_DB", str(HERE / "state" / "gigsim.db")))
ERROR_RATE = float(os.getenv("GIGSIM_ERROR_RATE", "0.03"))
CLIENT_MODE = os.getenv("GIGSIM_CLIENT_MODE", "rules")  # rules | llm
ADMIN_TOKEN = os.getenv("GIGSIM_ADMIN_TOKEN", "gigsim-admin")
RELEASE_EVERY_S = float(os.getenv("GIGSIM_RELEASE_SECONDS", "600"))
RELEASE_BATCH = int(os.getenv("GIGSIM_RELEASE_BATCH", "4"))
INITIAL_LIVE = int(os.getenv("GIGSIM_INITIAL_LIVE", "40"))
RESPONSE_DELAY_S = float(os.getenv("GIGSIM_RESPONSE_DELAY_SECONDS", "45"))
POLICY_ALL = yaml.safe_load((HERE / "policy.yaml").read_text())["versions"]
POLICY_VERSION = os.getenv("GIGSIM_POLICY_VERSION", max(POLICY_ALL))
POLICY = POLICY_ALL[POLICY_VERSION]
# Accounts: "token:user_id" pairs. Each token is one freelancer account on the simulated platform.
ACCOUNTS = dict(
    pair.split(":", 1) for pair in os.getenv("GIGSIM_TOKENS", "gs-dev-token:demo,gs-eval-token:eval").split(",") if ":" in pair
)

app = FastAPI(title="GigSim", version="1.2.2", description="Marketplace simulator for GigBid (synthetic).")
_lock = threading.RLock()
_hits: dict[str, deque] = {}
_rng = random.Random(int(os.getenv("GIGSIM_SEED", "794")))


# --------------------------------------------------------------------------------------------- storage
def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


CONN = db()
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, body TEXT, released_at REAL, live_order INTEGER);
CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS accounts (user_id TEXT PRIMARY KEY, connects INTEGER, week_start REAL);
CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, user_id TEXT, job_id TEXT, body TEXT,
  status TEXT, connects_spent INTEGER, created_at REAL, submitted_at REAL, decision TEXT, due_at REAL);
CREATE TABLE IF NOT EXISTS idem (key TEXT PRIMARY KEY, user_id TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, user_id TEXT, job_id TEXT, client_id TEXT,
  proposal_id TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS messages (message_id TEXT PRIMARY KEY, thread_id TEXT, sender TEXT, text TEXT,
  created_at REAL, due_at REAL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, type TEXT, ref TEXT,
  payload TEXT, created_at REAL, due_at REAL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _boot() -> None:
    with _lock:
        CONN.executescript(SCHEMA)
        if CONN.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
            return
        now = time.time()
        CONN.execute("BEGIN")
        jobs = json.loads((SEED / "jobs.json").read_text())
        live = json.loads((SEED / "live_stream.json").read_text())
        clients = json.loads((SEED / "clients.json").read_text())
        for j in jobs:
            CONN.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?)", (j["job_id"], json.dumps(j), now - 86400, None))
        for i, j in enumerate(live):
            rel = now - 3600 + i * 60 if i < INITIAL_LIVE else None
            CONN.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?)", (j["job_id"], json.dumps(j), rel, i))
        for cid, c in clients.items():
            CONN.execute("INSERT OR IGNORE INTO clients VALUES (?,?)", (cid, json.dumps(c)))
        CONN.execute("INSERT OR REPLACE INTO meta VALUES ('last_release', ?)", (str(now),))
        CONN.execute("COMMIT")


_boot()


def _release_live_jobs() -> None:
    """Release the next batch of the live stream every RELEASE_EVERY_S seconds (simulated new posts)."""
    now = time.time()
    last = float(CONN.execute("SELECT v FROM meta WHERE k='last_release'").fetchone()[0])
    batches = int((now - last) // RELEASE_EVERY_S)
    if batches <= 0:
        return
    rows = CONN.execute("SELECT job_id FROM jobs WHERE released_at IS NULL AND live_order IS NOT NULL "
                        "ORDER BY live_order LIMIT ?", (batches * RELEASE_BATCH,)).fetchall()
    for r in rows:
        CONN.execute("UPDATE jobs SET released_at=? WHERE job_id=?", (now, r[0]))
    CONN.execute("UPDATE meta SET v=? WHERE k='last_release'", (str(last + batches * RELEASE_EVERY_S),))


# ------------------------------------------------------------------------------------------ middleware
@app.middleware("http")
async def chaos_and_limits(request: Request, call_next):
    path = request.url.path
    if path.startswith("/v1/"):
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        window = _hits.setdefault(token, deque())
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= POLICY["rate_limit_per_minute"]:
            retry = max(1, int(60 - (now - window[0])))
            return JSONResponse({"error": "rate_limited", "retry_after": retry}, status_code=429,
                                headers={"Retry-After": str(retry)})
        window.append(now)
        if ERROR_RATE and _rng.random() < ERROR_RATE and request.headers.get("x-gigsim-no-chaos") != "1":
            return JSONResponse({"error": "upstream_unavailable"}, status_code=503)
    with _lock:
        _release_live_jobs()
        _deliver_due()
    return await call_next(request)


def user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if token not in ACCOUNTS:
        raise HTTPException(401, "invalid token")
    uid = ACCOUNTS[token]
    with _lock:
        row = CONN.execute("SELECT * FROM accounts WHERE user_id=?", (uid,)).fetchone()
        week = 7 * 86400
        if not row:
            CONN.execute("INSERT INTO accounts VALUES (?,?,?)", (uid, POLICY["connects"]["weekly_allowance"], time.time()))
        elif time.time() - row["week_start"] > week:
            CONN.execute("UPDATE accounts SET connects=?, week_start=? WHERE user_id=?",
                         (POLICY["connects"]["weekly_allowance"], time.time(), uid))
    return uid


def admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "admin only")


# -------------------------------------------------------------------------------------------- helpers
def _job(job_id: str) -> dict:
    row = CONN.execute("SELECT body, released_at FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row or row["released_at"] is None:
        raise HTTPException(404, "job not found")
    j = json.loads(row["body"])
    j["released_at"] = datetime.fromtimestamp(row["released_at"], UTC).isoformat()
    return j


def _client(cid: str) -> dict:
    row = CONN.execute("SELECT body FROM clients WHERE client_id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "client not found")
    return json.loads(row["body"])


def _public_client(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "hidden"}


def connects_for(job: dict) -> dict:
    base = int(job.get("connects_cost") or 4)
    cost, applied = base, []
    for rule in POLICY["connects"].get("surcharge_rules", []):
        w = rule["when"]
        if (job.get("budget") or 0) >= w.get("min_fixed_budget", math.inf) or \
                (job.get("hourly_high") or 0) >= w.get("min_hourly_high", math.inf):
            cost = math.ceil(cost * rule["multiplier"])
            applied.append(rule["name"])
    return {"base": base, "cost": cost, "surcharges": applied}


def _event(uid: str, typ: str, ref: str, payload: dict, due: float) -> None:
    CONN.execute("INSERT INTO events (user_id,type,ref,payload,created_at,due_at) VALUES (?,?,?,?,?,?)",
                 (uid, typ, ref, json.dumps(payload), time.time(), due))


def _idempotent(key: str | None, uid: str):
    if not key:
        raise HTTPException(400, "Idempotency-Key header is required on write calls")
    row = CONN.execute("SELECT response FROM idem WHERE key=? AND user_id=?", (key, uid)).fetchone()
    return json.loads(row[0]) if row else None


def _remember(key: str, uid: str, resp: dict) -> dict:
    CONN.execute("INSERT OR REPLACE INTO idem VALUES (?,?,?)", (key, uid, json.dumps(resp)))
    return resp


# ------------------------------------------------------------------------------- simulated client model
GENERIC_OPENERS = re.compile(r"^(dear (sir|madam|hiring)|hello,? i am|hi there!? i am|i am (very )?passionate|"
                             r"i am an expert|greetings|to whom it may concern|i have read your job)", re.I)
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\+?\d[\d\s-]{8,}\d|whatsapp|telegram|skype", re.I)


VENDOR = {"microsoft", "google", "amazon", "apple", "adobe", "the", "and", "for", "development", "developer"}


def _skill_hit(skill: str, low: str) -> float:
    """v1.2.2: credit for naming a must-have skill. The exact phrase scores 1; otherwise the share of its distinctive
    tokens present (6-letter stem match, vendor prefixes ignored), so "Excel" counts for "microsoft excel" and
    "financial models" half-counts for "financial analysis". v1.2.1 required the exact phrase."""
    skill = skill.lower()
    if skill in low:
        return 1.0
    toks = [t for t in re.findall(r"[a-z0-9+#.]+", skill) if t not in VENDOR and len(t) >= 2]
    if not toks:
        return 0.0
    words = set(re.findall(r"[a-z0-9+#.]+", low))
    stems = {w[:6] for w in words if len(w) >= 6}
    return sum(t in words or (len(t) >= 6 and t[:6] in stems) for t in toks) / len(toks)


def proposal_features(job: dict, client: dict, p: dict) -> dict:
    text = p["cover_letter"]
    low = text.lower()
    hidden = client["hidden"]
    must = hidden.get("must_have") or []
    title_words = {w for w in re.findall(r"[a-z]{5,}", job["title"].lower())}
    ceiling = hidden.get("hourly_ceiling") if job.get("is_hourly") else hidden.get("budget_ceiling")
    amount = p.get("bid_amount") or 0
    return {
        "generic_opening": bool(GENERIC_OPENERS.search(text.strip()[:120])),
        "must_have_hit": (sum(_skill_hit(m, low) for m in must) / len(must)) if must else 1.0,
        "evidence": len(re.findall(r"\[(?:case|repo|review|proposal|cv)[:\]]|portfolio://|case study|repository", low)),
        "personalised": len([w for w in title_words if w in low]) >= 2,
        "price_ratio": (amount / ceiling) if ceiling and amount else 1.0,
        "chars": len(text),
        "red_flags": bool(CONTACT.search(text)) or "guarantee" in low,
        "question": "?" in text[-300:],
    }


def reply_probability(f: dict, client: dict) -> float:
    h = client["hidden"]
    s = -0.6 + 1.3 * f["must_have_hit"] + 0.5 * f["personalised"] + 0.3 * f["question"]
    s += (0.9 if f["evidence"] else -0.4) * (1.0 if h.get("wants_evidence") else 0.4)
    s -= (1.3 if h.get("dislikes_generic_openings") else 0.3) * f["generic_opening"]
    s += 0.4 if f["price_ratio"] <= 1.0 else (-0.4 if f["price_ratio"] <= 1.2 else -1.4)
    s -= 0.5 if f["chars"] > 1800 else 0.0
    s -= 1.5 if f["red_flags"] else 0.0
    s += (h.get("responsiveness", 0.8) - 0.8)
    return 1 / (1 + math.exp(-(s - 0.4)))


def rule_decision(f: dict, client: dict, seed: str) -> str:
    h = client["hidden"]
    s = -0.6 + 1.3 * f["must_have_hit"] + 0.5 * f["personalised"] + 0.3 * f["question"]
    s += (0.9 if f["evidence"] else -0.4) * (1.0 if h.get("wants_evidence") else 0.4)
    s -= (1.3 if h.get("dislikes_generic_openings") else 0.3) * f["generic_opening"]
    s += 0.4 if f["price_ratio"] <= 1.0 else (-0.4 if f["price_ratio"] <= 1.2 else -1.4)
    s -= 0.5 if f["chars"] > 1800 else 0.0
    s -= 1.5 if f["red_flags"] else 0.0
    s += (h.get("responsiveness", 0.8) - 0.8)
    r = random.Random(seed).random()
    p_reply = 1 / (1 + math.exp(-(s - 0.4)))
    if r > p_reply:
        return "ignored"
    return "interview" if r < p_reply * 0.35 else "replied"


def llm_decision(job: dict, client: dict, p: dict, f: dict) -> tuple[str, str] | None:
    key, base = os.getenv("GIGSIM_LLM_API_KEY"), os.getenv("GIGSIM_LLM_BASE_URL", "https://api.groq.com/openai/v1")
    if not key:
        return None
    h = client["hidden"]
    prompt = (
        "You are a client on a freelance marketplace deciding what to do with one proposal. Your private "
        f"preferences: must-have skills {h.get('must_have')}; budget ceiling {h.get('budget_ceiling')} "
        f"(hourly ceiling {h.get('hourly_ceiling')}); you {'dislike' if h.get('dislikes_generic_openings') else 'tolerate'} "
        f"generic openings; you {'want' if h.get('wants_evidence') else 'do not need'} concrete evidence of past work.\n"
        f"Job title: {job['title']}\nYou received more than 30 proposals for this job and will reply to only a few. "
        f"Observations about this proposal: must-have skills addressed {f['must_have_hit']:.0%}; price is "
        f"{f['price_ratio']:.2f}x your ceiling; generic opening: {f['generic_opening']}; references to past work: "
        f"{f['evidence']}; length {f['chars']} characters.\nReply only if it clearly proves your must-have skills with "
        "specific past work, fits your budget and is written for YOUR job; otherwise ignore it.\n"
        f"Proposal (price {p.get('bid_amount')}):\n<<<\n{p['cover_letter'][:2500]}\n>>>\n"
        "Reply ONLY with JSON: "
        '{"decision": "ignored"|"replied"|"interview", "message": "<your reply to the freelancer, empty if ignored>"}'
    )
    try:
        r = httpx.post(f"{base}/chat/completions", timeout=40, headers={"Authorization": f"Bearer {key}"}, json={
            "model": os.getenv("GIGSIM_LLM_MODEL", "openai/gpt-oss-20b"), "temperature": 0,
            "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        d = out.get("decision")
        if d in ("ignored", "replied", "interview"):
            return d, out.get("message") or ""
    except Exception:  # noqa: BLE001 - simulator falls back to rules on any provider error
        return None
    return None


CLIENT_REPLIES = {
    "replied": [
        "Thanks for the proposal. Can you tell me how long the first milestone would take?",
        "Interesting. What would you need from us to get started, and could you do it for a bit less?",
        "Thanks. Have you done something similar for a client in our industry?",
    ],
    "interview": [
        "Your proposal stood out. Could we have a 30-minute call this week? Please suggest a couple of slots.",
        "I'd like to interview you. When are you available for a short video call?",
    ],
    "scam": [
        "Great, let's move this to Telegram, Upwork messages are slow. Add me @hr_desk_77 and send your bank details.",
    ],
}


def _deliver_due() -> None:
    now = time.time()
    due = CONN.execute("SELECT * FROM proposals WHERE status='submitted' AND due_at<=?", (now,)).fetchall()
    for p in due:
        body = json.loads(p["body"])
        job = json.loads(CONN.execute("SELECT body FROM jobs WHERE job_id=?", (p["job_id"],)).fetchone()[0])
        client = _client(job["client_id"])
        decision, message = json.loads(p["decision"])
        status = {"ignored": "viewed", "replied": "replied", "interview": "interview"}[decision]
        CONN.execute("UPDATE proposals SET status=? WHERE proposal_id=?", (status, p["proposal_id"]))
        _event(p["user_id"], "proposal_viewed", p["proposal_id"], {"job_id": p["job_id"]}, now)
        if decision != "ignored":
            tid = "th-" + hashlib.sha1(p["proposal_id"].encode()).hexdigest()[:10]
            CONN.execute("INSERT OR IGNORE INTO threads VALUES (?,?,?,?,?,?)",
                         (tid, p["user_id"], p["job_id"], client["client_id"], p["proposal_id"], decision))
            mid = "m-" + hashlib.sha1((tid + "first").encode()).hexdigest()[:10]
            CONN.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)", (mid, tid, "client", message, now, now))
            _event(p["user_id"], "client_replied" if decision == "replied" else "interview_requested", tid,
                   {"proposal_id": p["proposal_id"], "job_id": p["job_id"]}, now)
        _ = body
    pending = CONN.execute("SELECT * FROM messages WHERE due_at>? AND due_at<=? AND sender='client'", (now - 3600, now))
    for m in pending.fetchall():
        th = CONN.execute("SELECT * FROM threads WHERE thread_id=?", (m["thread_id"],)).fetchone()
        seen = CONN.execute("SELECT 1 FROM events WHERE type='client_message' AND ref=?", (m["message_id"],)).fetchone()
        if th and not seen and m["created_at"] != m["due_at"]:
            _event(th["user_id"], "client_message", m["message_id"], {"thread_id": th["thread_id"]}, now)


def _maybe_close_thread(tid: str, text: str, uid: str) -> None:
    """Client's next move after the freelancer replies: interview -> hire / lost, question -> follow-up."""
    th = CONN.execute("SELECT * FROM threads WHERE thread_id=?", (tid,)).fetchone()
    client = _client(th["client_id"])
    seed = tid + str(CONN.execute("SELECT COUNT(*) FROM messages WHERE thread_id=?", (tid,)).fetchone()[0])
    r = random.Random(seed).random()
    due = time.time() + RESPONSE_DELAY_S
    if client["hidden"].get("scammer"):
        reply, state = CLIENT_REPLIES["scam"][0], "scam"
    elif re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b.*\d{1,2}(:\d{2})?\s*(am|pm|utc|pkt)?", text, re.I) or \
            "slot" in text.lower():
        hired = r < 0.45 + 0.2 * (client["hidden"].get("responsiveness", 0.8) > 0.8)
        reply = ("The call was great - I'm sending you an offer now." if hired else
                 "Thanks for your time on the call. We decided to go with another freelancer.")
        state = "hired" if hired else "lost"
        if th["proposal_id"]:
            CONN.execute("UPDATE proposals SET status=? WHERE proposal_id=?", (state, th["proposal_id"]))
        _event(uid, "offer_received" if hired else "proposal_declined", th["proposal_id"] or tid,
               {"thread_id": tid}, due)
    else:
        reply = random.Random(seed).choice(CLIENT_REPLIES["interview"] + CLIENT_REPLIES["replied"][:1])
        state = "interview"
        if th["proposal_id"]:
            CONN.execute("UPDATE proposals SET status='interview' WHERE proposal_id=?", (th["proposal_id"],))
    mid = "m-" + hashlib.sha1((tid + seed).encode()).hexdigest()[:10]
    CONN.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)", (mid, tid, "client", reply, time.time(), due))
    CONN.execute("UPDATE threads SET state=? WHERE thread_id=?", (state, tid))


# ------------------------------------------------------------------------------------------- schemas
class ProposalIn(BaseModel):
    job_id: str
    cover_letter: str = Field(min_length=50, max_length=6000)
    screening_answers: list[str] = Field(default_factory=list, max_length=10)
    bid_amount: float = Field(gt=0, le=100000)
    bid_type: str = Field(pattern="^(fixed|hourly)$")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ------------------------------------------------------------------------------------------- routes
@app.get("/health")
def health():
    return {"status": "ok", "policy_version": POLICY_VERSION, "client_mode": CLIENT_MODE}


@app.get("/v1/policy")
def policy(_: str = Depends(user)):
    return {"version": POLICY_VERSION, **POLICY}


@app.get("/v1/jobs/search")
def search_jobs(q: str = "", category: str | None = None, domain: str | None = None, min_budget: float | None = None,
                posted_after: float | None = None, limit: int = Query(20, le=50), offset: int = 0,
                _: str = Depends(user)):
    rows = CONN.execute("SELECT body, released_at FROM jobs WHERE released_at IS NOT NULL AND released_at>=? "
                        "ORDER BY released_at DESC, job_id", (posted_after or 0,)).fetchall()
    terms = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 1]
    out = []
    for r in rows:
        j = json.loads(r["body"])
        hay = (j["title"] + " " + j["description"] + " " + " ".join(j["skills"])).lower()
        if terms and not any(t in hay for t in terms):
            continue
        if category and j["category"] != category:
            continue
        if domain and j.get("domain") != domain:
            continue
        if min_budget and (j.get("budget") or (j.get("hourly_high") or 0) * 40) < min_budget:
            continue
        j["released_at"] = datetime.fromtimestamp(r["released_at"], UTC).isoformat()
        out.append({k: j[k] for k in ("job_id", "title", "category", "skills", "is_hourly", "budget", "hourly_low",
                                      "hourly_high", "client_id", "connects_cost", "released_at", "domain")}
                   | {"snippet": j["description"][:240]})
    return {"total": len(out), "items": out[offset:offset + limit]}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, _: str = Depends(user)):
    j = _job(job_id)
    j["connects"] = connects_for(j)
    j.pop("domain", None)
    return j


@app.get("/v1/clients/{client_id}")
def client_history(client_id: str, _: str = Depends(user)):
    c = _public_client(_client(client_id))
    posted = CONN.execute("SELECT COUNT(*) FROM jobs WHERE body LIKE ?", (f'%"{client_id}"%',)).fetchone()[0]
    return {**c, "jobs_posted": max(posted, c.get("hires", 0))}


@app.get("/v1/connects")
def connects(uid: str = Depends(user)):
    row = CONN.execute("SELECT * FROM accounts WHERE user_id=?", (uid,)).fetchone()
    return {"balance": row["connects"], "weekly_allowance": POLICY["connects"]["weekly_allowance"],
            "resets_at": datetime.fromtimestamp(row["week_start"] + 7 * 86400, UTC).isoformat()}


@app.get("/v1/market/stats")
def market_stats(domain: str | None = None, _: str = Depends(user)):
    s = json.loads((SEED / "market_stats.json").read_text())
    return {"price": s["price"].get(domain) if domain else s["price"],
            "hourly_contract_rates": s["contract_hourly_rates"].get(domain) if domain else s["contract_hourly_rates"],
            "sources": s["sources"]}


@app.post("/v1/proposals")
def create_draft(p: ProposalIn, uid: str = Depends(user), idempotency_key: str | None = Header(default=None)):
    with _lock:
        if (hit := _idempotent(idempotency_key, uid)) is not None:
            return hit
        job = _job(p.job_id)
        pid = "pr-" + hashlib.sha1(f"{uid}{idempotency_key}".encode()).hexdigest()[:12]
        CONN.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (pid, uid, p.job_id, p.model_dump_json(), "draft", 0, time.time(), None, None, None))
        _event(uid, "proposal_drafted", pid, {"job_id": p.job_id}, time.time())
        return _remember(idempotency_key, uid, {"proposal_id": pid, "status": "draft",
                                                "connects": connects_for(job), "job_id": job["job_id"]})


@app.post("/v1/proposals/{proposal_id}/submit")
def submit(proposal_id: str, uid: str = Depends(user), idempotency_key: str | None = Header(default=None)):
    with _lock:
        if (hit := _idempotent(idempotency_key, uid)) is not None:
            return hit
        row = CONN.execute("SELECT * FROM proposals WHERE proposal_id=? AND user_id=?", (proposal_id, uid)).fetchone()
        if not row:
            raise HTTPException(404, "proposal not found")
        if row["status"] != "draft":  # a second submit with a NEW key is still not a double submit
            return {"proposal_id": proposal_id, "status": row["status"], "connects_spent": row["connects_spent"],
                    "already_submitted": True}
        body = json.loads(row["body"])
        limit = POLICY["proposal"].get("max_cover_letter_chars")
        if limit and len(body["cover_letter"]) > limit:
            raise HTTPException(422, f"cover letter exceeds {limit} characters (policy {POLICY_VERSION})")
        job = _job(row["job_id"])
        cost = connects_for(job)["cost"]
        acct = CONN.execute("SELECT connects FROM accounts WHERE user_id=?", (uid,)).fetchone()
        if acct["connects"] < cost:
            raise HTTPException(402, f"not enough Connects: need {cost}, have {acct['connects']}")
        client = _client(job["client_id"])
        f = proposal_features(job, client, body)
        llm = llm_decision(job, client, body, f) if CLIENT_MODE == "llm" else None
        if llm and llm[0] != "ignored":
            # calibration (v1.2.1): the LLM's "reply" is necessary but not sufficient; it is accepted with the rule
            # model's reply probability (seeded per proposal), which anchors the overall reply rate to a realistic level
            gate = min(1.0, max(0.15, 1.6 * reply_probability(f, client)))
            if random.Random(proposal_id + "gate").random() > gate:
                llm = ("ignored", "")
        decision = llm or (rule_decision(f, client, proposal_id), "")
        if not decision[1] and decision[0] != "ignored":
            pool = CLIENT_REPLIES["scam"] if client["hidden"].get("scammer") else CLIENT_REPLIES[decision[0]]
            decision = (decision[0], random.Random(proposal_id).choice(pool))
        due = time.time() + RESPONSE_DELAY_S
        CONN.execute("UPDATE accounts SET connects=connects-? WHERE user_id=?", (cost, uid))
        CONN.execute("UPDATE proposals SET status='submitted', connects_spent=?, submitted_at=?, decision=?, due_at=? "
                     "WHERE proposal_id=?", (cost, time.time(), json.dumps(decision), due, proposal_id))
        _event(uid, "proposal_submitted", proposal_id, {"job_id": job["job_id"], "connects": cost}, time.time())
        return _remember(idempotency_key, uid, {"proposal_id": proposal_id, "status": "submitted",
                                                "connects_spent": cost, "decision_mode": "llm" if llm else "rules"})


@app.get("/v1/proposals")
def list_proposals(uid: str = Depends(user)):
    rows = CONN.execute("SELECT proposal_id, job_id, status, connects_spent, created_at, submitted_at FROM proposals "
                        "WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/v1/threads")
def threads(uid: str = Depends(user)):
    now = time.time()
    out = []
    for t in CONN.execute("SELECT * FROM threads WHERE user_id=?", (uid,)).fetchall():
        msgs = CONN.execute("SELECT message_id, sender, text, created_at FROM messages WHERE thread_id=? AND due_at<=? "
                            "ORDER BY created_at", (t["thread_id"], now)).fetchall()
        out.append({**dict(t), "messages": [dict(m) for m in msgs]})
    return {"items": out}


@app.post("/v1/threads/{thread_id}/messages")
def send_message(thread_id: str, m: MessageIn, uid: str = Depends(user), idempotency_key: str | None = Header(default=None)):
    with _lock:
        if (hit := _idempotent(idempotency_key, uid)) is not None:
            return hit
        th = CONN.execute("SELECT * FROM threads WHERE thread_id=? AND user_id=?", (thread_id, uid)).fetchone()
        if not th:
            raise HTTPException(404, "thread not found")
        mid = "m-" + hashlib.sha1(f"{uid}{idempotency_key}".encode()).hexdigest()[:10]
        CONN.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (mid, thread_id, "freelancer", m.text, time.time(), time.time()))
        _maybe_close_thread(thread_id, m.text, uid)
        return _remember(idempotency_key, uid, {"message_id": mid, "thread_id": thread_id, "status": "sent"})


@app.get("/v1/events")
def events(since: int = 0, uid: str = Depends(user)):
    rows = CONN.execute("SELECT seq, type, ref, payload, created_at FROM events WHERE user_id=? AND seq>? AND due_at<=? "
                        "ORDER BY seq LIMIT 200", (uid, since, time.time())).fetchall()
    return {"items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]}


# --------------------------------------------------------------------------------------------- admin
class AdminJob(BaseModel):
    job: dict[str, Any]
    client: dict[str, Any]


@app.post("/admin/jobs", dependencies=[Depends(admin)])
def admin_add_job(items: list[AdminJob]):
    """Lets an external test runner add new jobs and clients (e.g. a hidden test set)."""
    with _lock:
        for it in items:
            CONN.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)", (it.job["job_id"], json.dumps(it.job), time.time(), None))
            CONN.execute("INSERT OR REPLACE INTO clients VALUES (?,?)", (it.client["client_id"], json.dumps(it.client)))
    return {"added": len(items)}


@app.post("/admin/fast_forward", dependencies=[Depends(admin)])
def fast_forward(seconds: float = 3600):
    """Move every pending response, message and event `seconds` into the past (evals don't wait)."""
    with _lock:
        for table in ("proposals", "messages", "events"):
            CONN.execute(f"UPDATE {table} SET due_at=due_at-? WHERE due_at IS NOT NULL", (seconds,))
        CONN.execute("UPDATE meta SET v=CAST(v AS REAL)-? WHERE k='last_release'", (seconds,))
        _release_live_jobs()
        _deliver_due()
    return {"ok": True}


@app.get("/admin/outcomes", dependencies=[Depends(admin)])
def outcomes(user_id: str | None = None):
    q = "SELECT proposal_id, user_id, job_id, status, connects_spent, decision FROM proposals"
    rows = CONN.execute(q + (" WHERE user_id=?" if user_id else ""), ((user_id,) if user_id else ())).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/admin/reset", dependencies=[Depends(admin)])
def reset(user_id: str):
    with _lock:
        for t in ("proposals", "threads", "events", "idem"):
            CONN.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
        CONN.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))
    return {"ok": True}
