"""Corpus registry for the Portfolio server: which folder backs which corpus, and its retriever.

  persona corpora : data/personas/<key>           (corpus id = key, e.g. "fullstack")
  user uploads    : $UPLOADS_DIR/<user_id>/       (corpus id = "user-<user_id>")
  standalone use  : $PORTFOLIO_DIR                (corpus id = $PORTFOLIO_CORPUS or "me")
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agent.settings import ROOT, settings
from rag.index.store import CorpusIndex, get_index
from rag.ingest.loaders import Document, load_portfolio
from rag.retriever import build_retriever

PERSONAS_DIR = ROOT / "data" / "personas"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", ROOT / "data" / "uploads"))
_lock = threading.RLock()
_docs: dict[str, dict[str, Document]] = {}


def default_corpus() -> str:
    return os.getenv("PORTFOLIO_CORPUS", "me" if os.getenv("PORTFOLIO_DIR") else "fullstack")


def corpus_dir(corpus: str) -> Path:
    if os.getenv("PORTFOLIO_DIR") and corpus == default_corpus():
        return Path(os.environ["PORTFOLIO_DIR"])
    if corpus.startswith("user-"):
        return UPLOADS_DIR / corpus.removeprefix("user-")
    return PERSONAS_DIR / corpus


def documents(corpus: str) -> dict[str, Document]:
    with _lock:
        if corpus not in _docs:
            d = corpus_dir(corpus)
            docs = load_portfolio(d, corpus) if d.exists() else []
            _docs[corpus] = {x.doc_id: x for x in docs}
        return _docs[corpus]


def index(corpus: str) -> CorpusIndex:
    rag = settings().rag
    scheme = rag["best"]["chunking"] if rag["variant"] == "best" else "structured"
    # under the lock: on a fresh store, concurrent first searches must not each embed the whole corpus
    with _lock:
        idx = get_index(corpus, scheme, rag["embed_model"])
        if idx.size == 0:
            docs = list(documents(corpus).values())
            if docs:
                idx.upsert_documents(docs)
    return idx


def reindex(corpus: str, changed: list[Document] | None = None, removed: list[str] | None = None) -> int:
    """Incremental re-indexing after a user adds/removes work in the UI."""
    with _lock:
        _docs.pop(corpus, None)
        idx = index(corpus)
        if removed:
            idx.delete_documents(removed)
        n = idx.upsert_documents(changed) if changed else 0
        return n


def retriever(corpus: str):
    index(corpus)  # make sure it exists
    return build_retriever(corpus)
