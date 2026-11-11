"""Graph state (C2).

Three things are kept apart on purpose:

  * STATE (this module): what one job thread or conversation needs right now. Checkpointed in Postgres.
  * EXECUTION TRACE (`exec_trace` + Phoenix spans + the MCP audit logs): every tool name, argument, result
    and observation. Append-only, for audit/debugging; nodes never read it to make decisions.
  * LONG-TERM MEMORY (agent/memory/): only promoted knowledge, written to the LangGraph store by the explicit
    rules in agent/memory/promotion_rules.py. Raw tool output never goes there.

Fields written by the three parallel branches (Job Analyst, Risk Assessor, Fit Matcher) each have exactly one
writer, and every field more than one node appends to has a reducer.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


def keep_last(a: Any, b: Any) -> Any:
    return b if b is not None else a


class BidState(TypedDict, total=False):
    # identity
    thread_id: str
    user_id: str
    corpus: str
    trace_id: str
    autonomy: str
    profile: dict          # freelancer rates/floor/domain (from user settings)
    # input
    job: dict
    duplicate_of: str
    client: dict
    # analyses (one writer each, run in parallel)
    job_spec: dict
    risk: dict
    fit: dict
    evidence: list[dict]
    exemplars: list[dict]
    # decisions and artefacts
    decision: dict
    price: dict
    drafts: Annotated[list[dict], operator.add]
    verification: Annotated[list[dict], operator.add]
    critiques: Annotated[list[dict], operator.add]
    compliance: Annotated[list[dict], operator.add]
    dry_run: dict
    approvals: Annotated[list[dict], operator.add]
    submission: dict
    outcome: dict
    revision: int
    # control
    status: Annotated[str, keep_last]
    attention_reason: str
    steps: Annotated[int, operator.add]
    usage: Annotated[dict, merge_dicts]
    errors: Annotated[list[str], operator.add]
    exec_trace: Annotated[list[dict], operator.add]
    memory_used: dict
    experiment_arm: str     # X2 proposal A/B arm


class ConversationState(TypedDict, total=False):
    thread_id: str
    user_id: str
    corpus: str
    trace_id: str
    marketplace_thread_id: str
    job: dict
    messages: list[dict]
    last_client_message: dict
    intent: dict
    slots: list[str]
    reply: dict
    compliance: Annotated[list[dict], operator.add]
    approvals: Annotated[list[dict], operator.add]
    sent: dict
    status: Annotated[str, keep_last]
    attention_reason: str
    steps: Annotated[int, operator.add]
    usage: Annotated[dict, merge_dicts]
    errors: Annotated[list[str], operator.add]
    exec_trace: Annotated[list[dict], operator.add]
