"""Pricing Strategist: a bid from market statistics, the client's budget and the user's floor, plus a Connects
decision (is the expected value worth the Connects?). Deterministic and fully explained; no LLM.

Connects follow the marketplace's active policy (the Connects figure GigSim/Upwork report for the job, which already
includes platform surcharges) and any surcharge rules mirrored in config (pricing.connects_surcharge_rules).
"""

from __future__ import annotations

import math

from agent.handoffs import PriceQuote
from agent.nodes.common import NodeContext, gigbid_node
from agent.settings import settings


def apply_config_surcharges(job: dict, base: int) -> tuple[int, list[str]]:
    cost, applied = base, []
    for rule in settings()["pricing"].get("connects_surcharge_rules") or []:
        w = rule["when"]
        if (job.get("budget") or 0) >= w.get("min_fixed_budget", math.inf) or \
                (job.get("hourly_high") or 0) >= w.get("min_hourly_high", math.inf):
            cost = math.ceil(cost * rule["multiplier"])
            applied.append(rule["name"])
    return cost, applied


def connects_for(job: dict) -> tuple[int, list[str]]:
    c = job.get("connects")
    if isinstance(c, dict) and c.get("cost") is not None:
        # the marketplace already applied its policy; never double-apply
        return int(c["cost"]), list(c.get("surcharges") or [])
    return apply_config_surcharges(job, int(job.get("connects_cost") or 4))


def quote(job: dict, fit: float, risk: float, client_score: float, profile: dict, stats: dict,
          balance: int | None) -> PriceQuote:
    floor_h = float(profile.get("hourly_floor", 20))
    rate = float(profile.get("hourly_rate", floor_h * 1.3))
    min_project = float(profile.get("min_project", 100))
    rationale: list[str] = []
    if job.get("is_hourly"):
        lo, hi = job.get("hourly_low"), job.get("hourly_high")
        mkt = stats.get("hourly_contract_rates") or {}
        target = rate * (0.9 + 0.2 * fit)  # stronger fit -> closer to (or above) the standard rate
        rationale.append(f"your standard rate ${rate:.0f}/h, adjusted for fit {fit:.2f} -> ${target:.0f}/h")
        if hi:
            target = min(target, hi)
            rationale.append(f"capped at the client's ceiling ${hi:.0f}/h")
        if lo and target < lo:
            target = lo
        if mkt.get("p75") and target > mkt["p75"] * 1.5:
            rationale.append(f"note: above 1.5x the market p75 (${mkt['p75']:.0f}/h) for this domain")
        amount = max(target, floor_h)
        if amount == floor_h:
            rationale.append(f"held at your floor ${floor_h:.0f}/h")
        low, high = max(floor_h, (lo or floor_h)), max(floor_h, hi or rate * 1.2)
        bid_type, value = "hourly", amount * 40
    else:
        budget = job.get("budget")
        fixed = (stats.get("price") or {}).get("fixed") or {}
        if budget:
            amount = budget * (0.92 + 0.12 * fit)
            rationale.append(f"client budget ${budget:,.0f}; bid {amount / budget:.0%} of it given fit {fit:.2f}")
        else:
            amount = max(min_project, fixed.get("p50", min_project))
            rationale.append(f"no budget stated; market median for similar jobs ${fixed.get('p50', 0):,.0f}")
        if amount < min_project:
            rationale.append(f"raised to your minimum project ${min_project:,.0f}")
            amount = min_project
        low, high = (budget or amount) * 0.8, (budget or amount) * 1.15
        bid_type, value = "fixed", amount
    connects, surcharges = connects_for(job)
    p_win = max(0.02, min(0.6, 0.05 + 0.35 * fit + 0.15 * client_score - 0.3 * risk))
    ev = p_win * value
    cost_usd = connects * settings()["pricing"]["connects_usd"]
    worth = ev >= settings()["pricing"]["min_expected_value_ratio"] * cost_usd
    rationale.append(f"{connects} Connects (${cost_usd:.2f}){' incl. ' + ', '.join(surcharges) if surcharges else ''}; "
                     f"win chance ~{p_win:.0%} -> expected value ${ev:,.0f}: {'worth it' if worth else 'not worth it'}")
    if balance is not None:
        rationale.append(f"Connects balance {balance} -> {balance - connects} after this bid")
    return PriceQuote(bid_type=bid_type, amount=round(amount, 2), low=round(low, 2), high=round(high, 2),
                      rationale=rationale, connects_cost=connects, connects_surcharges=surcharges,
                      expected_value_usd=round(ev, 2), connects_worth_it=worth)


@gigbid_node("pricing")
async def pricing(state: dict, ctx: NodeContext) -> dict:
    job = state["job"]
    tb = ctx.tools("pricing")
    profile = state.get("profile") or {}
    stats = await tb.call("marketplace", "get_market_stats", {"domain": profile.get("domain") or job.get("domain")})
    balance = (await tb.call("marketplace", "get_connects_balance", {})).get("balance")
    q = quote(job, state["fit"]["score"], state["risk"]["score"], state["risk"]["client"]["score"],
              profile, stats, balance)
    out = {"price": q.model_dump(), "_summary": f"{q.bid_type} ${q.amount:,.0f}; {q.connects_cost} Connects"}
    if balance is not None and balance < q.connects_cost:
        out.update(status="needs_attention", attention_reason=f"not enough Connects ({balance} < {q.connects_cost})")
    return out
