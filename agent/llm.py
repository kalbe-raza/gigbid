"""LLM access for every node: model routed by config, paced under free-tier limits, retried with backoff,
falling back to another provider, cached for evals, and accounted (tokens, latency, USD) per node.

    res = await chat("writer", messages, schema=ProposalDraftOut)
    res.parsed  # validated Pydantic object (JSON mode + one repair attempt)
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from agent.safety import mask_pii
from agent.settings import ROOT, settings
from agent.tracing import tracer


class LLMError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CallRecord:
    node: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cost_usd: float
    cached: bool = False
    fallback: bool = False


@dataclass
class Ledger:
    """Per-thread accounting; a node reads it to enforce the token budget."""
    calls: list[CallRecord] = field(default_factory=list)
    token_budget: int | None = None

    @property
    def tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)

    @property
    def cost(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)

    def by_node(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.calls:
            d = out.setdefault(c.node, {"calls": 0, "tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "models": set()})
            d["calls"] += 1
            d["tokens"] += c.prompt_tokens + c.completion_tokens
            d["cost_usd"] = round(d["cost_usd"] + c.cost_usd, 6)
            d["latency_s"] = round(d["latency_s"] + c.latency_s, 3)
            d["models"].add(c.model)
        return {k: {**v, "models": sorted(v["models"])} for k, v in out.items()}


current_ledger: contextvars.ContextVar[Ledger | None] = contextvars.ContextVar("ledger", default=None)


@dataclass
class LLMResult:
    text: str
    parsed: Any
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cost_usd: float
    cached: bool = False


# --------------------------------------------------------------------------------------------- pacing
class _Pacer:
    """Sliding-window token pacing per provider+model so free tiers are not tripped (8k TPM on Groq)."""

    def __init__(self) -> None:
        self.windows: dict[str, deque] = {}
        self.lock = asyncio.Lock()

    async def acquire(self, key: str, tokens: int, tpm: int) -> None:
        tokens = min(tokens, tpm)
        while True:
            async with self.lock:
                w = self.windows.setdefault(key, deque())
                now = time.monotonic()
                while w and now - w[0][0] > 60:
                    w.popleft()
                used = sum(t for _, t in w)
                if used + tokens <= tpm:
                    w.append((now, tokens))
                    return
                wait = 60 - (now - w[0][0]) + 0.05
            await asyncio.sleep(min(wait, 10))


PACER = _Pacer()


# ---------------------------------------------------------------------------------------------- cache
_CACHE_PATH = Path(os.getenv("LLM_CACHE_PATH", ROOT / "evals" / ".cache" / "llm.sqlite"))


def _cache() -> sqlite3.Connection | None:
    if os.getenv("LLM_CACHE") != "1":
        return None
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_CACHE_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT)")
    return c


def _ckey(model: str, messages: list[dict], extra: dict) -> str:
    return hashlib.sha256(json.dumps([model, messages, extra], sort_keys=True).encode()).hexdigest()


# -------------------------------------------------------------------------------------------- helpers
def _split(spec: str) -> tuple[str, str]:
    provider, model = spec.split(":", 1)
    return provider, model


def _provider(provider: str) -> tuple[str, str | None]:
    p = settings().models["providers"][provider]
    base = os.getenv(p.get("base_url_env", ""), "") or p["base_url"]
    key = os.getenv(p["key_env"]) if p.get("key_env") else None
    return base, key


def price(spec: str, pt: int, ct: int) -> float:
    pin, pout = settings().models.get("prices_usd_per_mtok", {}).get(spec, [0.0, 0.0])
    return round(pt / 1e6 * pin + ct / 1e6 * pout, 8)


def _extra_params(model: str, node: str) -> dict:
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}  # reasoning tokens count against max_tokens and the 8k TPM budget
    if "qwen" in model:
        return {"reasoning_effort": "none"}
    return {}


def extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


_OUTPUT_CAPS: dict[str, int] = {}  # per-request output caps learned from provider "request too large" answers


async def _post(spec: str, node: str, messages: list[dict], max_tokens: int, temperature: float,
                json_mode: bool) -> tuple[str, int, int]:
    provider, model = _split(spec)
    base, key = _provider(provider)
    if provider != "ollama" and not key:
        raise LLMError(f"no API key for provider {provider}")
    # some free-tier organisations cap output tokens per minute per model below what a node asks for (Groq answers
    # "Request too large"); models.max_output_tokens clamps the request so it can succeed
    max_tokens = min(max_tokens, settings().models.get("max_output_tokens", {}).get(spec, max_tokens),
                     _OUTPUT_CAPS.get(spec, max_tokens))
    body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                            "temperature": temperature, **_extra_params(model, node)}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    tpm = settings().models["tokens_per_minute"].get(provider, 10**9)
    est = sum(len(m["content"]) for m in messages) // 3 + max_tokens
    await PACER.acquire(spec, est, tpm)
    cfg = settings().marketplace["retries"]
    last: Exception | None = None
    attempt, rate_limited = 0, 0
    while attempt < cfg["attempts"]:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(f"{base}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {key}"} if key else {})
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = float(r.headers.get("retry-after", "0") or 0)
                if retry_after > 30:  # daily quota or long cool-down: let the caller fall back
                    raise LLMError(f"{spec} unavailable ({r.status_code}, retry-after {retry_after}s)")
                last = LLMError(f"{spec} HTTP {r.status_code}: {mask_pii(r.text[:240])}")
                if r.status_code == 429 and "too large" in r.text.lower():
                    # the account's per-minute output limit is below what this request reserves: shrink the request to
                    # the limit the provider reports (remembered for this process), otherwise it can never succeed
                    lim = re.search(r"\(OTPM\): Limit (\d+), Requested (\d+)", r.text)
                    if lim and int(lim.group(1)) < body["max_tokens"]:
                        _OUTPUT_CAPS[spec] = int(lim.group(1))
                        body["max_tokens"] = int(lim.group(1))
                        continue
                    raise last
                if r.status_code == 429 and rate_limited < 6:
                    # per-minute limit (TPM/RPM): the window frees up within a minute, so wait it out rather than
                    # spending the bounded attempts meant for server errors
                    rate_limited += 1
                    await asyncio.sleep(max(retry_after, 10.0))
                    continue
                await asyncio.sleep(max(retry_after, cfg["backoff_base_s"] * (2 ** attempt)))
                attempt += 1
                continue
            if r.status_code == 400 and json_mode and ("json_validate_failed" in r.text or "validate JSON" in r.text):
                # provider-side JSON validation failed (often truncation); retry once in plain mode, parse leniently
                body.pop("response_format", None)
                json_mode = False
                attempt += 1
                continue
            if r.status_code >= 400:
                raise LLMError(f"{spec} HTTP {r.status_code}: {mask_pii(r.text[:300])}")
            data = r.json()
            msg = data["choices"][0]["message"]
            usage = data.get("usage") or {}
            return (msg.get("content") or ""), int(usage.get("prompt_tokens", est)), int(usage.get("completion_tokens", 0))
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
            await asyncio.sleep(cfg["backoff_base_s"] * (2 ** attempt))
            attempt += 1
    raise LLMError(f"{spec} failed after retries: {last}")


async def chat(node: str, messages: list[dict], *, schema: type[BaseModel] | None = None, max_tokens: int = 900,
               temperature: float = 0.0, model: str | None = None) -> LLMResult:
    """Call the model configured for `node`. Falls back once to the profile's fallback model."""
    ledger = current_ledger.get()
    if ledger and ledger.token_budget and ledger.tokens >= ledger.token_budget:
        raise BudgetExceeded(f"token budget {ledger.token_budget} exhausted before {node}")
    primary = model or settings().model_for(node)
    chain = [primary]
    fb = settings().profile.get("fallback", {}).get("default")
    if fb and fb != primary:
        chain.append(fb)
    cache = _cache()
    ck = _ckey(primary, messages, {"schema": schema.__name__ if schema else None, "mt": max_tokens, "t": temperature})
    if cache:
        row = cache.execute("SELECT v FROM c WHERE k=?", (ck,)).fetchone()
        if row:
            v = json.loads(row[0])
            parsed = schema.model_validate(v["parsed"]) if schema and v.get("parsed") is not None else v.get("parsed")
            rec = CallRecord(node, v["model"], v["pt"], v["ct"], 0.0, price(v["model"], v["pt"], v["ct"]), cached=True)
            if ledger:
                ledger.calls.append(rec)
            return LLMResult(v["text"], parsed, v["model"], v["pt"], v["ct"], 0.0, rec.cost_usd, cached=True)

    errors = []
    for i, spec in enumerate(chain):
        t0 = time.perf_counter()
        with tracer().start_as_current_span(f"llm.{node}") as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("llm.model_name", spec)
            span.set_attribute("input.value", mask_pii(messages[-1]["content"])[:4000])
            try:
                msgs = list(messages)
                text, pt, ct = await _post(spec, node, msgs, max_tokens, temperature, schema is not None)
                parsed = None
                if schema is not None:
                    try:
                        parsed = schema.model_validate(extract_json(text))
                    except (ValidationError, json.JSONDecodeError, ValueError) as e:
                        repair = msgs + [{"role": "assistant", "content": text[:3000]},
                                         {"role": "user", "content": f"That was not valid against the schema: "
                                          f"{str(e)[:400]}. Reply with corrected JSON only."}]
                        text, pt2, ct2 = await _post(spec, node, repair, max_tokens, 0.0, True)
                        pt, ct = pt + pt2, ct + ct2
                        parsed = schema.model_validate(extract_json(text))
                lat = time.perf_counter() - t0
                cost = price(spec, pt, ct)
                span.set_attribute("llm.token_count.prompt", pt)
                span.set_attribute("llm.token_count.completion", ct)
                span.set_attribute("llm.token_count.total", pt + ct)
                span.set_attribute("llm.cost.total", cost)
                span.set_attribute("output.value", mask_pii(text)[:4000])
                rec = CallRecord(node, spec, pt, ct, round(lat, 3), cost, fallback=i > 0)
                if ledger:
                    ledger.calls.append(rec)
                if cache:
                    cache.execute("INSERT OR REPLACE INTO c VALUES (?,?)", (ck, json.dumps({
                        "text": text, "parsed": parsed.model_dump() if isinstance(parsed, BaseModel) else parsed,
                        "model": spec, "pt": pt, "ct": ct})))
                    cache.commit()
                return LLMResult(text, parsed, spec, pt, ct, round(lat, 3), cost)
            except (LLMError, ValidationError, json.JSONDecodeError, ValueError, KeyError) as e:
                span.set_attribute("error", mask_pii(str(e))[:300])
                errors.append(f"{spec}: {str(e)[:200]}")
                continue
    raise LLMError("; ".join(errors))


async def classify_injection(text: str) -> float | None:
    """Probability that `text` contains a prompt-injection attempt (Llama Prompt Guard 2). None if unavailable."""
    spec = settings().model_for("injection_classifier")
    try:
        out, pt, ct = await _post(spec, "injection_classifier", [{"role": "user", "content": text[:2000]}], 1, 0.0, False)
    except LLMError:
        return None
    ledger = current_ledger.get()
    if ledger:
        ledger.calls.append(CallRecord("injection_classifier", spec, pt, ct, 0.0, price(spec, pt, ct)))
    try:
        return float(out.strip())
    except ValueError:
        return None


def load_prompt(node: str) -> str:
    """Prompts live in agent/prompts/<node>.md (versioned, never inline)."""
    return (ROOT / "agent" / "prompts" / f"{node}.md").read_text()
