"""MultiServerMCPClient setup + per-agent tool allowlists (A6).

Agents never call GigSim, Upwork, a database or a file directly: every capability is an MCP tool, and each agent
gets only the tools named in ALLOWLIST. Calls have timeouts, bounded retries with backoff, and raise
`MCPUnavailable` (-> degraded mode: queue + banner + recover) when a server cannot be reached.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import timedelta
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.safety import mask_pii
from agent.tracing import current_trace_id, tracer

ALLOWLIST: dict[str, dict[str, list[str]]] = {
    "scout": {"marketplace": ["search_jobs", "get_job"]},
    "intake": {"marketplace": ["get_job", "get_client_history"]},
    "analyst": {},
    "risk_assessor": {"marketplace": ["get_client_history"]},
    "fit_matcher": {"portfolio": ["search_evidence", "list_skills"]},
    "pricing": {"marketplace": ["get_market_stats", "get_connects_balance", "get_policy"]},
    "writer": {"portfolio": ["search_evidence", "get_case_study"]},
    "verifier": {"portfolio": ["search_evidence"]},
    "critic": {"portfolio": ["search_evidence"]},
    "compliance": {},
    "dry_run": {"marketplace": ["create_proposal_draft"]},
    "submitter": {"marketplace": ["create_proposal_draft", "submit_proposal"]},
    "liaison": {"marketplace": ["list_messages", "get_job", "send_message"],
                "workspace": ["list_free_slots", "create_interview_hold", "create_email_draft"],
                "portfolio": ["search_evidence"]},
    "pipeline_sync": {"marketplace": ["list_events", "list_proposals", "list_messages"]},
    # R&D question C baselines get every tool on purpose (that is the point of the comparison)
    "baseline": {"marketplace": ["search_jobs", "get_job", "get_client_history", "get_market_stats",
                                 "get_connects_balance", "create_proposal_draft"],
                 "portfolio": ["search_evidence", "get_case_study", "list_skills"]},
}

TIMEOUTS_S = {"portfolio": 60, "marketplace": 30, "workspace": 15}


class MCPUnavailable(RuntimeError):
    def __init__(self, server: str, msg: str):
        super().__init__(f"{server} MCP server unavailable: {msg}")
        self.server = server


class ToolNotAllowed(PermissionError):
    pass


class ToolCallError(RuntimeError):
    def __init__(self, tool: str, msg: str):
        super().__init__(f"{tool}: {msg}")
        self.tool = tool
        self.unavailable = "marketplace_unavailable" in msg


def server_urls() -> dict[str, str]:
    return {
        "portfolio": os.getenv("PORTFOLIO_MCP_URL", "http://localhost:8101/mcp"),
        "marketplace": os.getenv("MARKETPLACE_MCP_URL", "http://localhost:8102/mcp"),
        "workspace": os.getenv("WORKSPACE_MCP_URL", "http://localhost:8103/mcp"),
    }


def connections(user_id: str, corpus: str | None, trace_id: str | None) -> dict[str, dict]:
    token = os.getenv("MCP_SERVICE_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}", "X-GigBid-User": user_id}
    if corpus:
        headers["X-GigBid-Corpus"] = corpus
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return {name: {"transport": "streamable_http", "url": url, "headers": dict(headers),
                   "timeout": timedelta(seconds=TIMEOUTS_S[name])}
            for name, url in server_urls().items()}


class ToolBox:
    """The only way a node reaches the outside world. Records every call for the execution trace."""

    def __init__(self, agent: str, user_id: str, corpus: str | None = None, trace_id: str | None = None,
                 attempts: int = 3, backoff: float = 0.8):
        self.agent = agent
        self.user_id = user_id
        self.corpus = corpus
        self.trace_id = trace_id or current_trace_id.get()
        self.allowed = ALLOWLIST.get(agent, {})
        self.attempts = attempts
        self.backoff = backoff
        self.calls: list[dict] = []
        self._client = MultiServerMCPClient(connections(user_id, corpus, self.trace_id))

    def allowed_tools(self) -> list[str]:
        return [t for tools in self.allowed.values() for t in tools]

    async def call(self, server: str, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool not in self.allowed.get(server, []):
            raise ToolNotAllowed(f"agent {self.agent!r} may not call {server}.{tool}")
        args = args or {}
        t0 = time.perf_counter()
        last: Exception | None = None
        with tracer().start_as_current_span(f"mcp.{server}.{tool}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("tool.name", tool)
            span.set_attribute("input.value", mask_pii(str(args))[:2000])
            for attempt in range(self.attempts):
                try:
                    async with self._client.session(server) as s:
                        res = await asyncio.wait_for(s.call_tool(tool, args, meta={"trace_id": self.trace_id}),
                                                     timeout=TIMEOUTS_S[server])
                    break
                except (TimeoutError, OSError, ConnectionError) as e:
                    last = e
                except Exception as e:  # noqa: BLE001 - httpx/anyio connection errors are wrapped in groups
                    if _is_connection_error(e):
                        last = e
                    else:
                        raise
                await asyncio.sleep(self.backoff * 2 ** attempt)
            else:
                self._record(server, tool, args, None, t0, ok=False, error=f"unavailable: {last!r}")
                span.set_attribute("error", "unavailable")
                raise MCPUnavailable(server, type(last).__name__ if last else "unknown")
            if res.isError:
                msg = " ".join(getattr(c, "text", "") for c in res.content)[:500]
                self._record(server, tool, args, None, t0, ok=False, error=msg)
                span.set_attribute("error", mask_pii(msg)[:300])
                err = ToolCallError(tool, msg)
                if err.unavailable:
                    raise MCPUnavailable(server, msg)
                raise err
            out = dict(res.structuredContent or {})
            if not out:
                import json

                text = " ".join(getattr(c, "text", "") for c in res.content)
                try:
                    out = json.loads(text)
                except json.JSONDecodeError:
                    out = {"text": text}
            span.set_attribute("output.value", mask_pii(str(out))[:2000])
            self._record(server, tool, args, out, t0, ok=True)
            return out

    def _record(self, server, tool, args, out, t0, ok: bool, error: str | None = None) -> None:
        summary = None
        if out is not None:
            s = str(out)
            summary = mask_pii(s[:300] + ("..." if len(s) > 300 else ""))
        self.calls.append({"agent": self.agent, "server": server, "tool": tool,
                           "args": {k: (mask_pii(v)[:200] if isinstance(v, str) else v) for k, v in args.items()},
                           "ok": ok, "error": mask_pii(error) if error else None, "result": summary,
                           "ms": round((time.perf_counter() - t0) * 1000, 1), "trace_id": self.trace_id})

    async def langchain_tools(self) -> list:
        """LangChain tools for prebuilt agents (R&D baselines), filtered to this agent's allowlist."""
        out = []
        for server, names in self.allowed.items():
            for t in await self._client.get_tools(server_name=server):
                if t.name in names:
                    out.append(t)
        return out


def _is_connection_error(e: BaseException) -> bool:
    import httpx

    seen = [e]
    while seen:
        x = seen.pop()
        if isinstance(x, httpx.TransportError | ConnectionError | OSError | TimeoutError):
            return True
        if isinstance(x, BaseExceptionGroup):
            seen.extend(x.exceptions)
        if x.__cause__:
            seen.append(x.__cause__)
    return False


async def server_health() -> dict[str, bool]:
    import httpx

    out = {}
    async with httpx.AsyncClient(timeout=3) as c:
        for name, url in server_urls().items():
            try:
                r = await c.get(url.rsplit("/mcp", 1)[0] + "/health")
                out[name] = r.status_code == 200
            except httpx.HTTPError:
                out[name] = False
    return out
