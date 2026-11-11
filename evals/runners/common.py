"""Shared helpers for every eval runner: dev data, in-memory indexes, a local tool box, metrics, result files.

Every number in README.md and the report is read from a JSON file this package writes under evals/results/.
"""

from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QDRANT_URL", "")          # evals use Qdrant's in-process mode
os.environ.setdefault("LLM_CACHE", "1")         # identical prompts are answered from the cache on reruns
os.environ.setdefault("TRACING_DISABLED", "1")
os.environ.setdefault("GIGSIM_DB", os.path.join(os.path.dirname(__file__), "..", ".cache", "gigsim_inproc.db"))

from agent.settings import ROOT  # noqa: E402

PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
RESULTS = ROOT / "evals" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


def dev(persona: str, split: str = "dev") -> list[dict]:
    path = ROOT / ("evals/datasets" if split == "dev" else "data/processed/heldout") / f"{split}_{persona}.jsonl"
    return [json.loads(line) for line in path.open()]


def sample(rows: list[dict], n: int | None, seed: int = 7, key=None) -> list[dict]:
    rows = [r for r in rows if key is None or key(r)]
    if n is None or n >= len(rows):
        return rows
    return random.Random(seed).sample(rows, n)


def build_indexes(personas=PERSONAS, schemes=("fixed", "structured"), models=None) -> None:
    from rag.index.store import get_index
    from rag.ingest.loaders import load_portfolio

    models = models or [__import__("agent.settings", fromlist=["settings"]).settings().rag["embed_model"]]
    for p in personas:
        docs = load_portfolio(ROOT / "data/personas" / p, p)
        for scheme in schemes:
            for m in models:
                idx = get_index(p, scheme, m)
                if idx.size == 0:
                    idx.upsert_documents(docs)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def write_result(name: str, payload: dict) -> Path:
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "commit": git_commit(),
               "hardware": f"{platform.machine()} {os.cpu_count()} CPU, {platform.system()}", **payload}
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, default=str))
    return path


def mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def pctl(xs, q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return round(xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f), 4)


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "tp": tp, "fp": fp, "fn": fn}


JOBS: dict[str, dict] = {}


def register_jobs(rows: list[dict]) -> None:
    for r in rows:
        JOBS[r["job"]["job_id"]] = r["job"]


def connects_for_policy(job: dict) -> dict:
    """Connects under GigSim's active policy, computed by GigSim's own function (no reimplementation)."""
    import importlib

    gs = importlib.import_module("gigsim.app")
    return gs.connects_for(job)


class LocalToolBox:
    """Same tool contract as agent.mcp_client.ToolBox, answered in-process (no HTTP) for fast, offline evals.
    Portfolio tools call the Portfolio server's own functions; marketplace reads come from the GigSim seed."""

    def __init__(self, persona: str):
        self.persona = persona
        self.calls: list[dict] = []
        self._clients = None

    async def call(self, server: str, tool: str, args: dict | None = None) -> dict[str, Any]:
        args = args or {}
        self.calls.append({"server": server, "tool": tool, "args": args})
        if server == "portfolio":
            from mcp_servers.portfolio import tools as T

            if tool == "search_evidence":
                f = T.EvidenceFilters(**args["filters"]) if args.get("filters") else None
                return T.search_evidence(self.persona, args["requirement"], args.get("k", 5), f).model_dump()
            if tool == "list_skills":
                return T.list_skills(self.persona).model_dump()
            if tool == "get_case_study":
                return T.get_case_study(self.persona, args["case_id"]).model_dump()
        if server == "marketplace":
            if self._clients is None:
                self._clients = json.loads((ROOT / "gigsim/seed/clients.json").read_text())
            if tool == "get_client_history":
                c = self._clients.get(args["client_id"], {})
                return {k: v for k, v in c.items() if k != "hidden"}
            if tool == "get_market_stats":
                s = json.loads((ROOT / "data/market/market_stats.json").read_text())
                d = args.get("domain")
                return {"price": s["price"].get(d), "hourly_contract_rates": s["contract_hourly_rates"].get(d)}
            if tool == "get_connects_balance":
                return {"balance": 80}
            if tool == "get_job":
                job = dict(JOBS[args["job_id"]])
                job["connects"] = connects_for_policy(job)
                return job
            if tool == "create_proposal_draft" and args.get("dry_run"):
                job = JOBS[args["job_id"]]
                conn = connects_for_policy(job)
                return {"dry_run": True, "status": "preview", "connects_cost": conn["cost"],
                        "connects_surcharges": conn["surcharges"], "connects_balance_after": 80 - conn["cost"],
                        "would_send": {k: args[k] for k in ("job_id", "cover_letter", "bid_amount", "bid_type",
                                                             "screening_answers") if k in args}, "policy_warnings": []}
        raise KeyError(f"LocalToolBox has no {server}.{tool}")
