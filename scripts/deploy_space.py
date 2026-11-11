"""Deploy GigBid to a free Hugging Face Docker Space (public URL, reviewer login preloaded with a persona).

    HF_TOKEN=... python -m scripts.deploy_space --space <hf-user>/gigbid --secrets-from .env

Uploads the repository (without raw Kaggle data, caches or secrets), writes the Space README front matter, and sets
secrets as Space secrets (never committed). The Space builds deploy/space/Dockerfile.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ["GROQ_API_KEY", "OPENROUTER_API_KEY", "APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "APPROVAL_SIGNING_KEY",
           "MCP_SERVICE_TOKEN", "REVIEWER_ACCOUNTS", "GIGSIM_ADMIN_TOKEN"]
IGNORE = [".venv/*", "**/node_modules/*", "apps/web/.next/*", "**/__pycache__/*", ".git/*", ".env", "data/processed/*",
          "data/uploads/*", "evals/.cache/*", "gigsim/state/*", "apps/web/test-results/*", "apps/web/playwright-report/*",
          "*.db", ".pytest_cache/*", ".ruff_cache/*"]

FRONT = """---
title: GigBid
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Multi-agent freelancer copilot - you approve every send
---

"""


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True)
    ap.add_argument("--secrets-from", default=".env")
    ap.add_argument("--no-secrets", action="store_true")
    a = ap.parse_args()
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    if not a.no_secrets:
        env = read_env(ROOT / a.secrets_from)
        for k in SECRETS:
            if env.get(k):
                api.add_space_secret(a.space, k, env[k])
        print("secrets set:", [k for k in SECRETS if env.get(k)])
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "space"
        shutil.copytree(ROOT, stage, ignore=shutil.ignore_patterns(".venv", "node_modules", ".next", "__pycache__",
                                                                   ".git", ".env", "processed", "uploads", ".cache",
                                                                   "state", "test-results", "playwright-report", "*.db"))
        shutil.copy(ROOT / "deploy/space/Dockerfile", stage / "Dockerfile")
        (stage / "README.md").write_text(FRONT + (ROOT / "README.md").read_text())
        api.upload_folder(repo_id=a.space, repo_type="space", folder_path=str(stage), ignore_patterns=IGNORE,
                          commit_message="Deploy GigBid")
    print(f"https://huggingface.co/spaces/{a.space}  ->  https://{a.space.replace('/', '-').lower()}.hf.space")


if __name__ == "__main__":
    main()
