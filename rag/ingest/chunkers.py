"""Chunkers. `fixed` is the V0 baseline; `structured` respects document structure and is used by V1+.

Every chunk carries the parent document's metadata (persona/corpus, type, project, skills, date,
client industry, outcome) so filters and outcome-aware ranking work at chunk level.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from rag.ingest.loaders import Document


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    meta: dict = field(default_factory=dict)


def _meta(d: Document) -> dict:
    return {"corpus": d.corpus, "type": d.type, "project": d.project, "skills": d.skills, "date": d.date,
            "client_industry": d.client_industry, "outcome": d.outcome, "title": d.title, "source": d.source}


def _cid(doc_id: str, i: int, text: str) -> str:
    return hashlib.sha1(f"{doc_id}|{i}|{text[:64]}".encode()).hexdigest()[:16]


def fixed(docs: list[Document], size: int = 800, overlap: int = 100) -> list[Chunk]:
    out = []
    for d in docs:
        t = d.text
        step = max(1, size - overlap)
        for i, start in enumerate(range(0, max(1, len(t)), step)):
            piece = t[start:start + size]
            if piece.strip():
                out.append(Chunk(_cid(d.doc_id, i, piece), d.doc_id, piece, _meta(d)))
            if start + size >= len(t):
                break
    return out


def structured(docs: list[Document], max_chars: int = 1200) -> list[Chunk]:
    """Markdown by heading, code file as one unit, CV by section, reviews/proposals whole.

    Each chunk is prefixed with a short header (title / project / type) so a fragment keeps its context."""
    out = []
    for d in docs:
        header = f"[{d.type}] {d.title}" + (f" | project: {d.project}" if d.project else "")
        if d.type in ("case_study", "readme"):
            parts = re.split(r"\n(?=#{1,3} )", d.text)
        elif d.type == "cv":
            parts = re.split(r"\n(?=(?:Summary|Core skills|Selected experience|Education)\n)", d.text)
        else:
            parts = [d.text]
        buf = ""
        pieces = []
        for p in parts:
            if len(buf) + len(p) > max_chars and buf:
                pieces.append(buf)
                buf = ""
            buf += ("\n" if buf else "") + p
        if buf:
            pieces.append(buf)
        for i, p in enumerate(pieces):
            for j in range(0, len(p), max_chars):
                text = f"{header}\n{p[j:j + max_chars]}"
                out.append(Chunk(_cid(d.doc_id, i * 100 + j, text), d.doc_id, text, _meta(d)))
    return out


CHUNKERS = {"fixed": fixed, "structured": structured}
