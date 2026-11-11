"""One index per (corpus, chunking scheme, embedding model): dense vectors in Qdrant + BM25 in memory.

Qdrant is the single vector store. Servers point QDRANT_URL at the compose service; evals and tests use
Qdrant's in-process ":memory:" mode through the same client API, so there is one code path.
BM25 is rebuilt from the Qdrant payloads, so Qdrant stays the source of truth across restarts.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from rag.index.embeddings import embed_documents, embed_query
from rag.ingest.chunkers import CHUNKERS, Chunk
from rag.ingest.loaders import Document

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9#+.\-/]*[a-z0-9#+]|[a-z0-9]")
STOP = {"the", "and", "for", "with", "you", "your", "our", "are", "this", "that", "will", "have", "from", "need",
        "who", "can", "able", "looking", "we", "a", "an", "to", "of", "in", "on", "is", "be", "or", "i", "it", "as"}


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


@lru_cache(maxsize=2)
def qdrant(url: str | None = None) -> QdrantClient:
    url = url if url is not None else os.getenv("QDRANT_URL", "")
    if url:
        return QdrantClient(url=url, timeout=30)
    path = os.getenv("QDRANT_PATH", "")
    return QdrantClient(path=path) if path else QdrantClient(location=":memory:")


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class CorpusIndex:
    def __init__(self, corpus: str, scheme: str, model: str, client: QdrantClient | None = None):
        self.corpus, self.scheme, self.model = corpus, scheme, model
        self.client = client or qdrant()
        safe = re.sub(r"[^a-z0-9]+", "_", f"{corpus}_{scheme}_{model}".lower())
        self.collection = f"gigbid_{safe}"
        self.chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ build / update
    def _ensure_collection(self, dim: int) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE))
            for field in ("doc_id", "type", "project", "outcome"):
                try:
                    self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)
                except Exception:  # noqa: BLE001 - in-memory mode ignores payload indexes
                    pass

    def upsert_documents(self, docs: list[Document]) -> int:
        """Incremental: re-chunks the given documents and replaces any earlier version of them."""
        if not docs:
            return 0
        chunks = CHUNKERS[self.scheme](docs)
        with self._lock:
            vecs = embed_documents([c.text for c in chunks], self.model)
            self._ensure_collection(vecs.shape[1])
            self.delete_documents([d.doc_id for d in docs], rebuild=False)
            self.client.upsert(self.collection, points=[
                models.PointStruct(id=_point_id(c.chunk_id), vector=v.tolist(),
                                   payload={"chunk_id": c.chunk_id, "doc_id": c.doc_id, "text": c.text, **c.meta})
                for c, v in zip(chunks, vecs, strict=True)])
            self._reload()
        return len(chunks)

    def delete_documents(self, doc_ids: list[str], rebuild: bool = True) -> None:
        if not doc_ids or not self.client.collection_exists(self.collection):
            return
        with self._lock:
            self.client.delete(self.collection, points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids))])))
            if rebuild:
                self._reload()

    def load(self) -> CorpusIndex:
        with self._lock:
            self._reload()
        return self

    def _reload(self) -> None:
        self.chunks = []
        if not self.client.collection_exists(self.collection):
            self._bm25 = None
            return
        offset = None
        while True:
            pts, offset = self.client.scroll(self.collection, limit=512, offset=offset, with_payload=True,
                                             with_vectors=False)
            for p in pts:
                pl = dict(p.payload)
                self.chunks.append(Chunk(pl.pop("chunk_id"), pl.pop("doc_id"), pl.pop("text"), pl))
            if offset is None:
                break
        self.chunks.sort(key=lambda c: c.chunk_id)
        self._bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks]) if self.chunks else None

    @property
    def size(self) -> int:
        return len(self.chunks)

    # ------------------------------------------------------------------ search
    def _filter(self, filters: dict | None):
        if not filters:
            return None
        must = []
        for k, v in filters.items():
            if isinstance(v, list):
                must.append(models.FieldCondition(key=k, match=models.MatchAny(any=v)))
            else:
                must.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))
        return models.Filter(must=must)

    def dense(self, query: str, k: int = 20, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        qv = embed_query(query, self.model)
        res = self.client.query_points(self.collection, query=qv.tolist(), limit=k, query_filter=self._filter(filters),
                                       with_payload=True).points
        out = []
        for p in res:
            pl = dict(p.payload)
            out.append((Chunk(pl.pop("chunk_id"), pl.pop("doc_id"), pl.pop("text"), pl), float(p.score)))
        return out

    def bm25(self, query: str, k: int = 20, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        order = scores.argsort()[::-1]
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            c = self.chunks[i]
            if filters and any((c.meta.get(fk) not in fv) if isinstance(fv, list) else c.meta.get(fk) != fv
                               for fk, fv in filters.items()):
                continue
            out.append((c, float(scores[i])))
            if len(out) >= k:
                break
        return out


_INDEXES: dict[tuple, CorpusIndex] = {}


def get_index(corpus: str, scheme: str, model: str) -> CorpusIndex:
    key = (corpus, scheme, model, os.getenv("QDRANT_URL", ""))
    if key not in _INDEXES:
        _INDEXES[key] = CorpusIndex(corpus, scheme, model).load()
    return _INDEXES[key]
