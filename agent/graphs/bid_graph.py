"""Bid graph (one thread per job), hand-written LangGraph StateGraph (R3, C1).

    intake ──Send──► job_analyst ─┐
           ├─Send──► risk_assessor ├─► decide ─bid─► pricing ─► writer ─► verifier ─► critic ─┐
           └─Send──► fit_matcher ──┘     │                         ▲                         │
                                         │                         └──── revise (max 2) ─────┤
                                    skip/watch                                               ▼
                                         ▼                         compliance ◄── apply_edit ◄┐
                                      finalize ◄── reject ── human_approval ◄── dry_run ◄─────┘ (allowed)
                                         ▲                         │ approve
                                         └────────── submit ◄──────┘
Blocked / needs-attention threads go straight to finalize; nothing downstream runs and nothing is sent.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from agent.nodes.analyst import job_analyst
from agent.nodes.approval import apply_edit, dry_run, finalize, human_approval, submit
from agent.nodes.common import STOP_STATUSES
from agent.nodes.compliance import compliance
from agent.nodes.critic import critic
from agent.nodes.decision import decision_node
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.pricing import pricing
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.scout import intake
from agent.nodes.verifier import verifier
from agent.nodes.writer import writer
from agent.state import BidState

PARALLEL = ["job_analyst", "risk_assessor", "fit_matcher"]


def fan_out(state: BidState):
    if state.get("status") in STOP_STATUSES:
        return "finalize"
    return [Send(name, state) for name in PARALLEL]


def after_decide(state: BidState) -> str:
    if state.get("status") in STOP_STATUSES:
        return "finalize"
    return "pricing" if (state.get("decision") or {}).get("action") == "bid" else "finalize"


def after_pricing(state: BidState) -> str:
    return "finalize" if state.get("status") in STOP_STATUSES else "writer"


def after_critic(state: BidState) -> str:
    if state.get("status") in STOP_STATUSES:
        return "finalize"
    return "writer" if state.get("status") == "revising" else "compliance"


def after_compliance(state: BidState) -> str:
    return "finalize" if state.get("status") in STOP_STATUSES else "dry_run"


def after_approval(state: BidState) -> str:
    return {"approved": "submit", "editing": "apply_edit"}.get(state.get("status", ""), "finalize")


def build_bid_graph(checkpointer=None, store=None):
    g = StateGraph(BidState)
    transient = RetryPolicy(max_attempts=2, initial_interval=1.0)
    g.add_node("intake", intake, retry_policy=transient)
    g.add_node("job_analyst", job_analyst)
    g.add_node("risk_assessor", risk_assessor, retry_policy=transient)
    g.add_node("fit_matcher", fit_matcher, retry_policy=transient)
    g.add_node("decide", decision_node)
    g.add_node("pricing", pricing, retry_policy=transient)
    g.add_node("writer", writer)
    g.add_node("verifier", verifier)
    g.add_node("critic", critic)
    g.add_node("compliance", compliance)
    g.add_node("dry_run", dry_run, retry_policy=transient)
    g.add_node("human_approval", human_approval)
    g.add_node("apply_edit", apply_edit)
    g.add_node("submit", submit, retry_policy=transient)
    g.add_node("finalize", finalize)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", fan_out, PARALLEL + ["finalize"])
    for n in PARALLEL:
        g.add_edge(n, "decide")
    g.add_conditional_edges("decide", after_decide, ["pricing", "finalize"])
    g.add_conditional_edges("pricing", after_pricing, ["writer", "finalize"])
    g.add_edge("writer", "verifier")
    g.add_edge("verifier", "critic")
    g.add_conditional_edges("critic", after_critic, ["writer", "compliance", "finalize"])
    g.add_conditional_edges("compliance", after_compliance, ["dry_run", "finalize"])
    g.add_edge("dry_run", "human_approval")
    g.add_conditional_edges("human_approval", after_approval, ["submit", "apply_edit", "finalize"])
    g.add_edge("apply_edit", "compliance")
    g.add_edge("submit", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer, store=store, name="bid_graph")
