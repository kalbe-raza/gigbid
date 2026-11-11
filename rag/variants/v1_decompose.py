"""V1 (query group, required): requirement decomposition. One retrieval per extracted requirement, merged with
reciprocal rank fusion. Requirements come from the Job Analyst's JobSpec when available, otherwise from
`heuristic_requirements` (specific skills + requirement-like sentences)."""

from rag.variants.base import VariantConfig

CONFIG = VariantConfig(name="v1_decompose", chunking="structured", query="decomposed", retrieval="dense")
