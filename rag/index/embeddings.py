"""Open embedding and reranking models (CPU, ONNX via fastembed). Loaded once per process."""

from __future__ import annotations

import os
import threading
from functools import lru_cache

import numpy as np

_lock = threading.Lock()
CACHE_DIR = os.getenv("FASTEMBED_CACHE", os.path.expanduser("~/.cache/fastembed"))

# fastembed needs the query/document prefixes these models were trained with
PREFIXES = {
    "nomic-ai/nomic-embed-text-v1.5-Q": ("search_query: ", "search_document: "),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
    "BAAI/bge-small-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
}


@lru_cache(maxsize=4)
def _embedder(name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=name, cache_dir=CACHE_DIR, threads=int(os.getenv("EMBED_THREADS", "2")))


@lru_cache(maxsize=4)
def _reranker(name: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=name, cache_dir=CACHE_DIR, threads=int(os.getenv("EMBED_THREADS", "2")))


def embed_documents(texts: list[str], model: str) -> np.ndarray:
    pre = PREFIXES.get(model, ("", ""))[1]
    with _lock:
        vecs = list(_embedder(model).embed([pre + t for t in texts], batch_size=16))
    return _normalise(np.array(vecs, dtype=np.float32))


@lru_cache(maxsize=20000)
def _embed_query_cached(text: str, model: str) -> tuple[float, ...]:
    pre = PREFIXES.get(model, ("", ""))[0]
    with _lock:
        v = next(iter(_embedder(model).embed([pre + text])))
    return tuple(_normalise(np.array([v], dtype=np.float32))[0].tolist())


def embed_query(text: str, model: str) -> np.ndarray:
    return np.array(_embed_query_cached(text, model), dtype=np.float32)


_RERANK_CACHE: dict[tuple[str, str, str], float] = {}


def rerank(query: str, docs: list[str], model: str) -> list[float]:
    """Cross-encoder scores; (query, doc) pairs are cached in-process (variants re-ask the same pairs)."""
    if not docs:
        return []
    todo = [d for d in dict.fromkeys(docs) if (model, query, d) not in _RERANK_CACHE]
    if todo:
        with _lock:
            scores = [float(s) for s in _reranker(model).rerank(query, todo, batch_size=8)]
        if len(_RERANK_CACHE) > 400_000:
            _RERANK_CACHE.clear()
        _RERANK_CACHE.update({(model, query, d): sc for d, sc in zip(todo, scores, strict=True)})
    return [_RERANK_CACHE[(model, query, d)] for d in docs]


def _normalise(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=1, keepdims=True)
    n[n == 0] = 1
    return a / n
