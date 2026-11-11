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


def embed_query(text: str, model: str) -> np.ndarray:
    pre = PREFIXES.get(model, ("", ""))[0]
    with _lock:
        v = next(iter(_embedder(model).embed([pre + text])))
    return _normalise(np.array([v], dtype=np.float32))[0]


def rerank(query: str, docs: list[str], model: str) -> list[float]:
    if not docs:
        return []
    with _lock:
        return [float(s) for s in _reranker(model).rerank(query, docs, batch_size=8)]


def _normalise(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=1, keepdims=True)
    n[n == 0] = 1
    return a / n
