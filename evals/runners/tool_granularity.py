"""R&D question A: does tool granularity change agent behaviour?

40 tasks with a known correct call, each answered by the same model under three tool designs:
  fine    16 narrow tools (one field each)
  coarse  3 umbrella tools taking an `action` argument
  best    GigBid's real tool set, with descriptions rewritten after a first pass (what the MCP servers expose)
Metrics: tool-selection accuracy (first call is an acceptable tool), argument accuracy (required argument values
present and correct), tokens per task (prompt + completion, from provider usage).

    python -m evals.runners.tool_granularity [--model groq:qwen/qwen3.8-27b]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random

import httpx

from agent.llm import PACER, _provider, _split
from agent.settings import ROOT, settings
from evals.runners.common import PERSONAS, dev, mean, wilson, write_result

CACHE = ROOT / "evals/.cache/tool_granularity.json"


def fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


S = {"type": "string"}
TOOLSETS = {
    "fine": [
        fn("get_job_title", "Job title", {"job_id": S}, ["job_id"]),
        fn("get_job_description", "Job description text", {"job_id": S}, ["job_id"]),
        fn("get_job_budget", "Job budget", {"job_id": S}, ["job_id"]),
        fn("get_job_skills", "Skills listed on a job", {"job_id": S}, ["job_id"]),
        fn("get_client_rating", "Client rating", {"client_id": S}, ["client_id"]),
        fn("get_client_spend", "Client total spend", {"client_id": S}, ["client_id"]),
        fn("get_client_payment_status", "Whether client payment is verified", {"client_id": S}, ["client_id"]),
        fn("get_connects_balance", "Connects balance", {}),
        fn("get_hourly_rate_stats", "Hourly rate stats", {"domain": S}, ["domain"]),
        fn("get_fixed_price_stats", "Fixed price stats", {"domain": S}, ["domain"]),
        fn("search_job_titles", "Search jobs by title words", {"query": S}, ["query"]),
        fn("search_case_studies", "Search case studies", {"query": S}, ["query"]),
        fn("search_repositories", "Search code repositories", {"query": S}, ["query"]),
        fn("search_reviews", "Search client reviews", {"query": S}, ["query"]),
        fn("read_case_study", "Read a case study", {"case_id": S}, ["case_id"]),
        fn("get_free_slots", "Free calendar slots", {"duration_minutes": {"type": "integer"}}),
    ],
    "coarse": [
        fn("marketplace", "Marketplace operations", {"action": {"type": "string", "enum": [
            "search_jobs", "get_job", "get_client", "get_connects", "get_market_stats"]}, "params": {"type": "object"}},
           ["action"]),
        fn("portfolio", "Portfolio operations", {"action": {"type": "string", "enum": ["search", "get_case_study",
                                                                                       "list_skills"]},
                                                 "params": {"type": "object"}}, ["action"]),
        fn("workspace", "Calendar and email operations", {"action": {"type": "string", "enum": ["free_slots", "hold",
                                                                                             "email_draft"]},
                                                          "params": {"type": "object"}}, ["action"]),
    ],
    "best": [
        fn("search_jobs", "Search open marketplace jobs by keywords and/or domain (ai_ml, fullstack, data_analyst). "
           "Use for finding NEW jobs; returns summaries.", {"query": S, "domain": S, "limit": {"type": "integer"}}),
        fn("get_job", "Full post for ONE job id: description, budget, skills and Connects cost.", {"job_id": S}, ["job_id"]),
        fn("get_client_history", "A client's track record: payment verified, total spent, rating, hires. Use to judge "
           "whether a client is trustworthy.", {"client_id": S}, ["client_id"]),
        fn("get_connects_balance", "The user's remaining Connects this week.", {}),
        fn("get_market_stats", "Typical prices for a domain: hourly and fixed-price percentiles.", {"domain": S}, ["domain"]),
        fn("search_evidence", "Find portfolio evidence (case studies, repos, reviews, CV) proving ONE requirement or "
           "skill.", {"requirement": S, "k": {"type": "integer"}}, ["requirement"]),
        fn("get_case_study", "Full text of one case study by id (e.g. case:helpdesk-rag or helpdesk-rag).",
           {"case_id": S}, ["case_id"]),
        fn("list_free_slots", "Free interview slots in working hours.", {"duration_minutes": {"type": "integer"},
                                                                        "days_ahead": {"type": "integer"}}),
    ],
}


def tasks(seed: int = 40) -> list[dict]:
    rng = random.Random(seed)
    rows = [r for p in PERSONAS for r in dev(p)[:60]]
    projects = {p: json.loads((ROOT / f"data/personas/{p}/_gold/projects.json").read_text()) for p in PERSONAS}
    out = []
    for _i in range(5):
        r = rng.choice(rows)
        j, c = r["job"]["job_id"], r["job"]["client_id"]
        p = rng.choice(PERSONAS)
        pid = rng.choice(list(projects[p]))
        skill = rng.choice(["Celery", "Power BI", "React", "LangChain", "dbt", "Stripe", "PyTorch", "Tableau"])
        q = rng.choice(["react dashboard", "machine learning model", "power bi report", "django api"])
        out += [
            {"type": "read_job", "prompt": f"Show me the full post for job {j}.",
             "ok": {"fine": ["get_job_description"], "coarse": [("marketplace", "get_job")], "best": ["get_job"]},
             "args": {"job_id": j}},
            {"type": "client_trust", "prompt": f"Is the client {c} trustworthy? Check their history before I bid.",
             "ok": {"fine": ["get_client_rating", "get_client_spend", "get_client_payment_status"],
                    "coarse": [("marketplace", "get_client")], "best": ["get_client_history"]}, "args": {"client_id": c}},
            {"type": "connects", "prompt": "How many Connects do I have left this week?",
             "ok": {"fine": ["get_connects_balance"], "coarse": [("marketplace", "get_connects")],
                    "best": ["get_connects_balance"]}, "args": {}},
            {"type": "market_rate", "prompt": f"What do {p.replace('_', ' ')} jobs usually pay per hour? Domain id is {p}.",
             "ok": {"fine": ["get_hourly_rate_stats"], "coarse": [("marketplace", "get_market_stats")],
                    "best": ["get_market_stats"]}, "args": {"domain": p}},
            {"type": "find_jobs", "prompt": f"Find me new jobs about {q}.",
             "ok": {"fine": ["search_job_titles"], "coarse": [("marketplace", "search_jobs")], "best": ["search_jobs"]},
             "args": {"query~": q.split()[0]}},
            {"type": "evidence", "prompt": f"What proof do I have that I've worked with {skill}?",
             "ok": {"fine": ["search_case_studies", "search_repositories", "search_reviews"],
                    "coarse": [("portfolio", "search")], "best": ["search_evidence"]}, "args": {"query~": skill.lower()}},
            {"type": "case_study", "prompt": f"Open my case study '{pid}' so I can reread it.",
             "ok": {"fine": ["read_case_study"], "coarse": [("portfolio", "get_case_study")], "best": ["get_case_study"]},
             "args": {"case_id~": pid}},
            {"type": "slots", "prompt": "When am I free for a 30-minute interview call this week?",
             "ok": {"fine": ["get_free_slots"], "coarse": [("workspace", "free_slots")], "best": ["list_free_slots"]},
             "args": {"duration_minutes": 30}},
        ]
    return out


def judge(task: dict, design: str, call: dict | None) -> tuple[bool, bool]:
    if not call:
        return False, False
    name, args = call["name"], call["args"]
    if design == "coarse":
        ok_tool = any(name == t and args.get("action") == a for t, a in task["ok"]["coarse"])
        args = {**(args.get("params") or {}), **{k: v for k, v in args.items() if k not in ("action", "params")}}
    else:
        ok_tool = name in task["ok"][design]
    ok_args = ok_tool
    for k, v in task["args"].items():
        if k.endswith("~"):
            vals = " ".join(str(x).lower() for x in args.values())
            ok_args = ok_args and str(v).lower() in vals
        elif k == "duration_minutes":
            ok_args = ok_args and int(args.get(k, 30) or 30) == v
        else:
            ok_args = ok_args and str(args.get(k, "")).strip() == str(v)
    return ok_tool, ok_args


async def ask(spec: str, prompt: str, tools: list, cache: dict) -> dict:
    key = hashlib.sha256(json.dumps([spec, prompt, tools], sort_keys=True).encode()).hexdigest()
    if key in cache:
        return cache[key]
    provider, model = _split(spec)
    base, k = _provider(provider)
    body = {"model": model, "messages": [
        {"role": "system", "content": "You are a freelancer's assistant. Use exactly one tool call to start answering."},
        {"role": "user", "content": prompt}], "tools": tools, "tool_choice": "auto", "max_tokens": 400, "temperature": 0}
    if "qwen" in model:
        body["reasoning_effort"] = "none"
    await PACER.acquire(spec, len(json.dumps(body)) // 3 + 400, settings().models["tokens_per_minute"][provider])
    for _attempt in range(4):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {k}"})
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("retry-after", 5)) + 1)
            continue
        if r.status_code == 400 and "tool_use_failed" in r.text:
            # the model emitted a malformed / non-existent tool call; the provider rejects it - a real failure
            fg = r.json().get("error", {}).get("failed_generation", "")
            out = {"call": None, "tokens": len(json.dumps(body)) // 4, "prompt_tokens": None,
                   "error": "tool_use_failed", "failed_generation": str(fg)[:300]}
            cache[key] = out
            CACHE.write_text(json.dumps(cache))
            return out
        r.raise_for_status()
        d = r.json()
        tc = (d["choices"][0]["message"].get("tool_calls") or [None])[0]
        call = None
        if tc:
            try:
                call = {"name": tc["function"]["name"], "args": json.loads(tc["function"]["arguments"] or "{}")}
            except json.JSONDecodeError:
                call = {"name": tc["function"]["name"], "args": {}}
        out = {"call": call, "tokens": d["usage"]["total_tokens"], "prompt_tokens": d["usage"]["prompt_tokens"]}
        cache[key] = out
        CACHE.write_text(json.dumps(cache))
        return out
    raise RuntimeError("rate limited")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="groq:qwen/qwen3.8-27b")
    a = ap.parse_args()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    ts = tasks()
    results, per_task = {}, []
    for design, tools in TOOLSETS.items():
        sel, arg, toks = [], [], []
        for t in ts:
            res = await ask(a.model, t["prompt"], tools, cache)
            ok_t, ok_a = judge(t, design, res["call"])
            sel.append(ok_t)
            arg.append(ok_a)
            toks.append(res["tokens"])
            per_task.append({"design": design, "type": t["type"], "prompt": t["prompt"], "call": res["call"],
                             "tool_ok": ok_t, "args_ok": ok_a, "tokens": res["tokens"]})
        by_type = {}
        for typ in sorted({t["type"] for t in ts}):
            idx = [i for i, t in enumerate(ts) if t["type"] == typ]
            by_type[typ] = {"tool_acc": mean(sel[i] for i in idx), "arg_acc": mean(arg[i] for i in idx)}
        results[design] = {"n_tools": len(tools), "n_tasks": len(ts), "tool_selection_accuracy": mean(sel),
                           "tool_ci95": wilson(sum(sel), len(sel)), "argument_accuracy": mean(arg),
                           "arg_ci95": wilson(sum(arg), len(arg)), "tokens_per_task": mean(toks), "by_task_type": by_type}
        print(design, json.dumps({k: v for k, v in results[design].items() if k != "by_task_type"}), flush=True)
    write_result("tool_granularity", {
        "question": "Does tool granularity change agent behaviour?", "model": a.model, "results": results,
        "hypothesis": "H-A: the curated set with explicit 'use this when' descriptions beats both extremes on argument "
                      "accuracy; coarse tools cost the fewest tokens but lose argument accuracy to nested params.",
        "per_task": per_task})
    os.makedirs(ROOT / "evals/results", exist_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
