"""Typed access to agent/config.yaml (+ environment). Import `settings()` anywhere; it is cached."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

try:  # local development convenience; containers get real env vars
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    raw: dict

    @property
    def marketplace(self) -> dict:
        return self.raw["marketplace"]

    @property
    def models(self) -> dict:
        return self.raw["models"]

    @property
    def profile(self) -> dict:
        name = os.getenv("MODEL_PROFILE", self.models["profile"])
        return self.models["profiles"][name]

    def model_for(self, node: str) -> str:
        return self.profile["nodes"][node]

    @property
    def rag(self) -> dict:
        return self.raw["rag"]

    @property
    def thresholds(self) -> dict:
        return self.raw["thresholds"]

    @property
    def budgets(self) -> dict:
        return self.raw["budgets"]

    def __getitem__(self, key: str):
        return self.raw[key]


@lru_cache(maxsize=1)
def settings() -> Settings:
    path = Path(os.getenv("GIGBID_CONFIG", ROOT / "agent" / "config.yaml"))
    raw = yaml.safe_load(path.read_text())
    if os.getenv("MARKETPLACE_MODE"):
        raw["marketplace"]["mode"] = os.environ["MARKETPLACE_MODE"]
    if os.getenv("RAG_VARIANT"):
        raw["rag"]["variant"] = os.environ["RAG_VARIANT"]
    return Settings(raw)


def reload_settings() -> Settings:
    settings.cache_clear()
    return settings()
