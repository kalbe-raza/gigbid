"""V4 (ranking group): outcome-aware ranking. Evidence from projects that past WON or REPLIED proposals cited
gets a prior boost; the writer also receives the most similar winning proposals as phrasing exemplars."""

from rag.variants.base import VariantConfig

CONFIG = VariantConfig(name="v4_outcome", chunking="structured", query="whole", retrieval="hybrid", rerank="cross",
                       outcome=True)
