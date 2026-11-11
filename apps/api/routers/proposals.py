"""Proposal studio: the pending approval payload (draft, evidence per claim, critic + compliance notes, dry-run
preview) and single-paragraph regeneration."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from agent import runner
from agent.llm import LLMError, chat
from agent.tracing import trace_url
from apps.api import db
from apps.api.auth.users import CurrentUser
from apps.api.schemas.models import ParagraphIn, ParagraphOut, StudioPayload

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


async def _thread(user: dict, job_id: str) -> str:
    r = await db.fetchone("SELECT thread_id FROM inbox WHERE user_id=%s AND job_id=%s", user["user_id"], job_id)
    if not r or not r["thread_id"]:
        raise HTTPException(404, "no analysis for this job yet")
    return r["thread_id"]


@router.get("/{job_id}", response_model=StudioPayload)
async def studio(job_id: str, user=CurrentUser):
    tid = await _thread(user, job_id)
    snap = await runner.snapshot("bid", tid)
    v = snap["values"]
    approval = snap["interrupts"][0] if snap["interrupts"] else None
    return StudioPayload(thread_id=tid, status="awaiting_approval" if approval else v.get("status", "unknown"),
                         pending=approval is not None, approval=approval, drafts=v.get("drafts") or [],
                         critiques=v.get("critiques") or [], verification=v.get("verification") or [],
                         compliance=v.get("compliance") or [], submission=v.get("submission"),
                         attention_reason=v.get("attention_reason"), trace_url=trace_url(v.get("trace_id")))


@router.post("/{job_id}/regenerate", response_model=ParagraphOut)
async def regenerate_paragraph(job_id: str, body: ParagraphIn, user=CurrentUser):
    """Rewrite ONE paragraph with the same evidence. The result goes into the editor; it is only used if the user
    then approves an edit (which is re-checked by Compliance)."""
    tid = await _thread(user, job_id)
    v = (await runner.snapshot("bid", tid))["values"]
    if not v.get("drafts"):
        raise HTTPException(409, "no draft yet")
    paras = re.split(r"\n\s*\n", v["drafts"][-1]["cover_letter"].strip())
    if body.paragraph_index >= len(paras):
        raise HTTPException(400, "no such paragraph")
    ev = "\n".join(f"[{e['doc_id']}] {(e.get('snippet') or '')[:300]}" for e in (v.get("evidence") or [])[:10])
    msg = (f"Rewrite paragraph {body.paragraph_index + 1} of this proposal. Keep every factual sentence cited with an "
           f"evidence id from the list; do not add claims the evidence does not support. "
           f"{('Instruction from the freelancer: ' + body.instruction) if body.instruction else ''}\n\nEVIDENCE:\n{ev}\n\n"
           f"FULL PROPOSAL:\n{v['drafts'][-1]['cover_letter']}\n\nPARAGRAPH TO REWRITE:\n{paras[body.paragraph_index]}\n\n"
           "Reply with the new paragraph only.")
    try:
        res = await chat("writer", [{"role": "user", "content": msg}], max_tokens=900, temperature=0.6)
    except LLMError as e:
        raise HTTPException(503, f"model unavailable: {str(e)[:120]}") from e
    return ParagraphOut(paragraph_index=body.paragraph_index, text=res.text.strip())
