"""B4 decisions: bid-or-skip F1, scam precision/recall, price inside the fair band, duplicate detection, and cost/
latency per job analysed. Uses the graph's own node functions (fit matcher, risk assessor, decide, pricing) with an
in-process tool box, so the numbers are what the deployed graph would decide.

    python -m evals.runners.decision_eval [--limit N] [--risk-llm never|ambiguous|always] [--tag name]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from agent.llm import Ledger, current_ledger
from agent.nodes.decision import decide
from agent.nodes.fit_matcher import match
from agent.nodes.pricing import quote
from agent.nodes.risk_assessor import assess
from agent.nodes.scout import find_duplicate
from agent.settings import ROOT
from evals.runners.common import (
    PERSONAS,
    LocalToolBox,
    build_indexes,
    dev,
    mean,
    pctl,
    prf,
    sample,
    wilson,
    write_result,
)


async def run_job(row: dict, persona: str, profile: dict, clients: dict, stats: dict, risk_llm: str,
                  seen: list[dict]) -> dict:
    job = row["job"]
    led = Ledger()
    tok = current_ledger.set(led)
    t0 = time.perf_counter()
    try:
        dup = find_duplicate(job, seen)
        tb = LocalToolBox(persona)
        client = {k: v for k, v in clients.get(job.get("client_id"), {}).items() if k != "hidden"}
        fit = await match(job, tb)
        risk = await assess(job, client, risk_llm)
        d = decide(job, risk.model_dump(), fit.model_dump(), profile, "manual", dup)
        price = None
        if d.action == "bid":
            price = quote(job, fit.score, risk.score, risk.client.score, profile,
                          {"price": stats["price"][persona], "hourly_contract_rates": stats["contract_hourly_rates"][persona]},
                          80)
    finally:
        current_ledger.reset(tok)
    g = row["gold"]
    band = g["fair_price"]
    in_band = None
    if price is not None and g["decision"] == "bid":
        in_band = band["low"] <= price.amount <= band["high"] and price.bid_type == band["type"]
    return {"persona": persona, "job_id": job["job_id"], "kind": g["kind"].split(":")[0], "gold": g["decision"],
            "pred": d.action, "pred_bid": d.action == "bid", "gold_scam": g["scam"], "pred_scam": risk.is_scam,
            "injection": bool(g["injection"]), "pred_injection": risk.injection_detected, "duplicate_gold": g["duplicate_of"],
            "duplicate_pred": dup, "fit": fit.score, "risk": risk.score, "price": price.amount if price else None,
            "price_in_band": in_band, "reasons": d.reasons[:2], "tokens": led.tokens, "cost_usd": led.cost,
            "latency_s": round(time.perf_counter() - t0, 3)}


def summarise(rows: list[dict]) -> dict:
    tp = sum(r["pred_bid"] and r["gold"] == "bid" for r in rows)
    fp = sum(r["pred_bid"] and r["gold"] != "bid" for r in rows)
    fn = sum(not r["pred_bid"] and r["gold"] == "bid" for r in rows)
    st = sum(r["pred_scam"] and r["gold_scam"] for r in rows)
    sf = sum(r["pred_scam"] and not r["gold_scam"] for r in rows)
    sn = sum(not r["pred_scam"] and r["gold_scam"] for r in rows)
    band = [r["price_in_band"] for r in rows if r["price_in_band"] is not None]
    dups = [r for r in rows if r["duplicate_gold"]]
    inj = [r for r in rows if r["injection"]]
    return {
        "n": len(rows), "bid_skip": prf(tp, fp, fn),
        "accuracy": round(sum((r["pred"] == "bid") == (r["gold"] == "bid") for r in rows) / len(rows), 4) if rows else None,
        "scam": prf(st, sf, sn),
        "price_in_fair_band": {"rate": mean(band), "n": len(band), "ci95": wilson(sum(band), len(band))},
        "duplicates_caught": {"rate": mean([bool(r["duplicate_pred"]) for r in dups]), "n": len(dups)},
        "injection_detected": {"rate": mean([r["pred_injection"] for r in inj]), "n": len(inj),
                               "false_positive_rate": mean([r["pred_injection"] for r in rows if not r["injection"]])},
        "per_job": {"tokens_mean": mean(r["tokens"] for r in rows), "usd_mean": mean(r["cost_usd"] for r in rows),
                    "latency_p50_s": pctl([r["latency_s"] for r in rows], .5),
                    "latency_p95_s": pctl([r["latency_s"] for r in rows], .95)},
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--risk-llm", default="ambiguous", choices=["never", "ambiguous", "always"])
    ap.add_argument("--tag", default="")
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    a = ap.parse_args()
    build_indexes(schemes=("structured",))
    clients = json.loads((ROOT / "gigsim/seed/clients.json").read_text())
    stats = json.loads((ROOT / "data/market/market_stats.json").read_text())
    rows = []
    for p in PERSONAS:
        profile = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text()) | {"domain": p}
        data = dev(p, a.split)
        if a.limit:
            data = sample(data, a.limit, seed=3)
        seen: list[dict] = []
        for i, r in enumerate(data):
            res = await run_job(r, p, profile, clients, stats, a.risk_llm, seen)
            seen.append({"job_id": r["job"]["job_id"], "title": r["job"]["title"], "description": r["job"]["description"]})
            rows.append(res)
            if i % 25 == 0:
                print(p, i, res["gold"], res["pred"], f"fit={res['fit']:.2f}", flush=True)
    out = {"risk_llm": a.risk_llm, "split": a.split, "overall": summarise(rows),
           "by_persona": {p: summarise([r for r in rows if r["persona"] == p]) for p in PERSONAS},
           "by_kind": {k: summarise([r for r in rows if r["kind"] == k]) for k in sorted({r["kind"] for r in rows})},
           "errors": [r for r in rows if (r["pred"] == "bid") != (r["gold"] == "bid")][:60]}
    name = f"decisions{'_' + a.split if a.split != 'dev' else ''}_{a.risk_llm}{'_' + a.tag if a.tag else ''}"
    write_result(name, out)
    (ROOT / f"evals/results/{name}_per_job.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    o = out["overall"]
    print(json.dumps({"bid_f1": o["bid_skip"]["f1"], "scam": o["scam"], "price_in_band": o["price_in_fair_band"]}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
