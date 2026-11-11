"""Node runtime shared by both graphs.

`@gigbid_node("writer")` wraps a node function with:
  * budget checks: step budget and token budget per thread -> status "needs_attention" (visible in the UI)
  * a no-op pass-through once a thread is blocked / needs attention (nothing downstream runs, nothing is sent)
  * per-node timeout and bounded retries on transient LLM errors
  * execution-trace capture (MCP calls + node record) and live progress events for the SSE stream
Marketplace / MCP outages propagate as MCPUnavailable so the runner can queue the job (degraded mode).
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from agent.llm import BudgetExceeded, Ledger, LLMError, current_ledger
from agent.mcp_client import ToolBox
from agent.settings import settings

STOP_STATUSES = {"needs_attention", "blocked", "dead_letter"}
NODE_TIMEOUT_S = {"writer": 180, "critic": 120, "verifier": 120, "liaison": 120, "fit_matcher": 150}


class NodeContext:
    def __init__(self, name: str, state: dict, config: RunnableConfig):
        conf = (config or {}).get("configurable", {})
        self.name = name
        self.state = state
        self.user_id = state.get("user_id") or conf.get("user_id", "local")
        self.corpus = state.get("corpus") or conf.get("corpus")
        self.trace_id = state.get("trace_id") or conf.get("trace_id")
        self.thread_id = state.get("thread_id") or conf.get("thread_id")
        self.toolboxes: list[ToolBox] = []

    def tools(self, agent: str | None = None) -> ToolBox:
        import os

        if os.getenv("GIGBID_LOCAL_TOOLS") == "1":  # evals: same tool contract, answered in-process (no HTTP)
            from evals.runners.common import LocalToolBox

            tb = LocalToolBox(self.corpus or "fullstack")
            self.toolboxes.append(tb)  # type: ignore[arg-type]
            return tb  # type: ignore[return-value]
        tb = ToolBox(agent or self.name, self.user_id, self.corpus, self.trace_id)
        self.toolboxes.append(tb)
        return tb

    def emit(self, kind: str, **payload: Any) -> None:
        try:
            get_stream_writer()({"node": self.name, "kind": kind, **payload})
        except Exception:  # noqa: BLE001 - outside a streaming run there is no writer
            pass


def fill(template: str, **kw: Any) -> str:
    """Fill {placeholders} without touching JSON braces in the prompt."""
    for k, v in kw.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def gigbid_node(name: str, retries: int = 1):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(state: dict, config: RunnableConfig | None = None) -> dict:
            if state.get("status") in STOP_STATUSES:
                return {}
            budgets = settings().budgets
            steps = int(state.get("steps") or 0)
            if steps + 1 > budgets["max_graph_steps"]:
                return {"status": "needs_attention", "attention_reason": f"step budget ({budgets['max_graph_steps']}) "
                        f"exceeded before {name}"}
            ledger = current_ledger.get()
            if ledger is None:
                ledger = Ledger()
                current_ledger.set(ledger)
            conversation = "marketplace_thread_id" in state
            ledger.token_budget = budgets["max_tokens_per_conversation"] if conversation else budgets["max_tokens_per_job"]
            ctx = NodeContext(name, state, config or {})
            ctx.emit("started")
            t0 = time.perf_counter()
            calls_before = len(ledger.calls)
            attempt = 0
            while True:
                try:
                    out = await asyncio.wait_for(fn(state, ctx), timeout=NODE_TIMEOUT_S.get(name, 90))
                    break
                except BudgetExceeded as e:
                    ctx.emit("needs_attention", reason=str(e))
                    return {"status": "needs_attention", "attention_reason": str(e), "steps": 1}
                except (LLMError, TimeoutError) as e:
                    attempt += 1
                    if attempt > retries:
                        raise
                    ctx.emit("retry", error=str(e)[:200])
                    await asyncio.sleep(2 * attempt)
            out = dict(out or {})
            new_calls = ledger.calls[calls_before:]
            usage = {
                "tokens": sum(c.prompt_tokens + c.completion_tokens for c in new_calls),
                "cost_usd": round(sum(c.cost_usd for c in new_calls), 6),
                "llm_calls": len(new_calls),
                "models": sorted({c.model for c in new_calls}),
                "latency_s": round(time.perf_counter() - t0, 3),
            }
            trace = [c for tb in ctx.toolboxes for c in tb.calls]
            trace.append({"agent": name, "node": name, "kind": "node", "usage": usage, "trace_id": ctx.trace_id})
            out["steps"] = 1
            out["usage"] = {f"{name}:{steps + 1}": usage}  # keyed per execution: revisions don't overwrite
            out["exec_trace"] = out.get("exec_trace", []) + trace
            ctx.emit("finished", usage=usage, summary=out.pop("_summary", None))
            return out

        wrapper.node_name = name
        return wrapper

    return deco
