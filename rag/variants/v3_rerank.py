"""V3 (retrieval group, required): cross-encoder reranking of the 30 hybrid candidates for precision at small k."""

from rag.variants.base import VariantConfig

CONFIG = VariantConfig(name="v3_rerank", chunking="structured", query="whole", retrieval="hybrid", rerank="cross")
