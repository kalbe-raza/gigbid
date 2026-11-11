"""V2 (retrieval group, required): hybrid BM25 + dense, fused with reciprocal rank fusion (k=60).
Exact tool names ("Celery", "dbt", "Pinecone") that embeddings blur are caught by BM25."""

from rag.variants.base import VariantConfig

CONFIG = VariantConfig(name="v2_hybrid", chunking="structured", query="whole", retrieval="hybrid")
