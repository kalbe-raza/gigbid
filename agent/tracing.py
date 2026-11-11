"""Tracing with self-hosted Arize Phoenix (OpenTelemetry + OpenInference).

One trace id flows UI -> API -> graph -> MCP servers -> audit log:
  * the API reads/creates `x-trace-id` and opens a root span with that id (apps/api/main.py)
  * LangGraph/LangChain spans are auto-instrumented (openinference-instrumentation-langchain)
  * every LLM call opens an LLM span with token counts and cost (agent/llm.py)
  * MCP calls carry the trace id in `_meta.trace_id` and in the `x-trace-id` header; servers log it.
PII is masked before anything is attached to a span.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_initialised = False


def init_tracing(service: str) -> None:
    global _initialised
    if _initialised or os.getenv("TRACING_DISABLED") == "1":
        return
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        return
    from phoenix.otel import register

    from agent.settings import settings

    tp = register(project_name=os.getenv("PHOENIX_PROJECT_NAME", settings()["tracing"]["project"]),
                  endpoint=endpoint.rstrip("/") + "/v1/traces", batch=True, set_global_tracer_provider=True,
                  verbose=False)
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=tp)
    except Exception:  # noqa: BLE001 - tracing must never take the app down
        pass
    _initialised = True


def tracer():
    return trace.get_tracer("gigbid")


def new_trace_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def trace_root(name: str, trace_id: str | None = None, **attrs):
    """Open a span whose OTel trace id equals our trace id, so Phoenix links match the audit log."""
    tid = trace_id or current_trace_id.get() or new_trace_id()
    token = current_trace_id.set(tid)
    parent = trace.set_span_in_context(NonRecordingSpan(SpanContext(
        trace_id=int(tid, 16), span_id=int(uuid.uuid4().hex[:16], 16), is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED))))
    try:
        with tracer().start_as_current_span(name, context=parent) as span:
            span.set_attribute("gigbid.trace_id", tid)
            span.set_attribute("openinference.span.kind", attrs.pop("kind", "CHAIN"))
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(f"gigbid.{k}", str(v))
            yield tid
    finally:
        current_trace_id.reset(token)


def trace_url(trace_id: str | None) -> str | None:
    base = os.getenv("PHOENIX_PUBLIC_URL")
    if not base or not trace_id:
        return None
    return f"{base.rstrip('/')}/redirects/traces/{trace_id}"
