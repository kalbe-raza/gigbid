"""Retriever factory: the deployed variant is chosen by config (agent/config.yaml -> rag.variant), never in code.

    from rag.retriever import build_retriever
    r = build_retriever(corpus="fullstack")            # uses the configured variant
    r = build_retriever(corpus="fullstack", variant="v0_naive")
"""

from __future__ import annotations

import dataclasses
import importlib

from agent.settings import settings
from rag.variants.base import Pipeline, VariantConfig

VARIANTS = ["v0_naive", "v1_decompose", "v2_hybrid", "v3_rerank", "v4_outcome", "v8_corrective", "best"]


def variant_config(name: str) -> VariantConfig:
    rag = settings().rag
    if name == "best":
        cfg = VariantConfig(name="best", **rag["best"])
    else:
        cfg = importlib.import_module(f"rag.variants.{name}").CONFIG
    return dataclasses.replace(cfg, embed_model=rag["embed_model"], rerank_model=rag["rerank_model"])


def build_retriever(corpus: str, variant: str | None = None, **overrides) -> Pipeline:
    cfg = variant_config(variant or settings().rag["variant"])
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    return Pipeline(cfg, corpus)
