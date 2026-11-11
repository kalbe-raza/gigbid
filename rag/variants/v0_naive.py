"""V0 (baseline, required): fixed 800-char chunks, top-k cosine with the whole job post as the query."""

from rag.variants.base import VariantConfig

CONFIG = VariantConfig(name="v0_naive", chunking="fixed", query="whole", retrieval="dense")
