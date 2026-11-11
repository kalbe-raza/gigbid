"""Conversation graph (one thread per client conversation), C1.

    load_thread ─► classify_intent ─┬─ interview_request ─► propose_slots ─┐
                                    └─ question / negotiation / scam ───────┴─► draft_reply ─► message_compliance
    message_compliance ─(allowed)─► message_approval (interrupt) ─approve─► send_reply ─► END
                                          │ edit ─► message_compliance           │ reject / blocked ─► END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agent.nodes.common import STOP_STATUSES
from agent.nodes.liaison import (
    classify_intent,
    draft_reply,
    load_thread,
    message_approval,
    message_compliance,
    propose_slots,
    send_reply,
)
from agent.state import ConversationState


def after_classify(state: ConversationState) -> str:
    if state.get("status") in STOP_STATUSES:
        return END
    return "propose_slots" if (state.get("intent") or {}).get("intent") == "interview_request" else "draft_reply"


def after_compliance(state: ConversationState) -> str:
    return END if state.get("status") in STOP_STATUSES else "message_approval"


def after_approval(state: ConversationState) -> str:
    return {"approved": "send_reply", "compliance": "message_compliance"}.get(state.get("status", ""), END)


def build_conversation_graph(checkpointer=None, store=None):
    g = StateGraph(ConversationState)
    transient = RetryPolicy(max_attempts=2, initial_interval=1.0)
    g.add_node("load_thread", load_thread, retry_policy=transient)
    g.add_node("classify_intent", classify_intent)
    g.add_node("propose_slots", propose_slots, retry_policy=transient)
    g.add_node("draft_reply", draft_reply)
    g.add_node("message_compliance", message_compliance)
    g.add_node("message_approval", message_approval)
    g.add_node("send_reply", send_reply, retry_policy=transient)
    g.add_edge(START, "load_thread")
    g.add_edge("load_thread", "classify_intent")
    g.add_conditional_edges("classify_intent", after_classify, ["propose_slots", "draft_reply", END])
    g.add_edge("propose_slots", "draft_reply")
    g.add_edge("draft_reply", "message_compliance")
    g.add_conditional_edges("message_compliance", after_compliance, ["message_approval", END])
    g.add_conditional_edges("message_approval", after_approval, ["send_reply", "message_compliance", END])
    g.add_edge("send_reply", END)
    return g.compile(checkpointer=checkpointer, store=store, name="conversation_graph")
