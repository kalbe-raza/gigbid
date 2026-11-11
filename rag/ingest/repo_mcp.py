"""Repository ingestion through a THIRD-PARTY MCP server (A5): the official MCP reference filesystem server
(`@modelcontextprotocol/server-filesystem`, run with npx, read-only use, rooted at the repos directory only).

    python -m rag.ingest.repo_mcp data/personas/fullstack/repos --corpus fullstack     (prints what it ingested)

The Portfolio server uses `mcp_repo_loader` when REPO_INGEST=mcp (default when `npx` is available) and falls back to
the local loader otherwise. Trust rationale: see docs/TOOLS.md.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

from rag.ingest.loaders import CODE_EXT, Document, repo_documents

PKG = "@modelcontextprotocol/server-filesystem@2025.8.21"


def available() -> bool:
    return shutil.which("npx") is not None


async def read_repos(repos_root: Path, corpus: str, max_files: int = 40) -> list[Document]:
    root = repos_root.resolve()
    client = MultiServerMCPClient({"fs": {"transport": "stdio", "command": "npx", "args": ["-y", PKG, str(root)]}})
    docs: list[Document] = []
    async with client.session("fs") as s:
        tree = await s.call_tool("directory_tree", {"path": str(root)})
        nodes = json.loads(tree.content[0].text)
        for repo in [n for n in nodes if n.get("type") == "directory"]:
            files: dict[str, str] = {}

            def walk(prefix: str, children: list) -> None:
                for c in children:
                    rel = f"{prefix}{c['name']}"
                    if c.get("type") == "directory":
                        walk(rel + "/", c.get("children", []))
                    elif (Path(rel).suffix in CODE_EXT or c["name"] == "Dockerfile") and len(files) < max_files:
                        files[rel] = ""

            walk("", repo.get("children", []))
            paths = [str(root / repo["name"] / rel) for rel in files]
            if not paths:
                continue
            res = await s.call_tool("read_multiple_files", {"paths": paths})
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            # server returns "<path>:\n<content>\n---\n<path>:\n..." for multiple files
            for block in text.split("\n---\n"):
                head, _, body = block.partition(":\n")
                rel = str(Path(head.strip()).resolve().relative_to(root / repo["name"])) if head.strip() else ""
                if rel in files:
                    files[rel] = body
            docs.extend(repo_documents(repo["name"], {k: v for k, v in files.items() if v}, corpus,
                                       f"mcp+fs://{repo['name']}"))
    return docs


def mcp_repo_loader_factory():
    """Returns a per-repo loader for load_portfolio() that reads through the MCP server (whole repos dir once)."""
    cache: dict[Path, list[Document]] = {}

    def loader(repo_path: Path, corpus: str) -> list[Document]:
        root = repo_path.parent
        if root not in cache:
            cache[root] = asyncio.run(read_repos(root, corpus))
        return [d for d in cache[root] if d.project == repo_path.name]

    return loader


if __name__ == "__main__":
    path = Path(sys.argv[1])
    corpus = sys.argv[sys.argv.index("--corpus") + 1] if "--corpus" in sys.argv else "local"
    ds = asyncio.run(read_repos(path, corpus))
    for d in ds:
        print(f"{d.doc_id:<45} {d.type:<7} skills={d.skills}")
    print(f"{len(ds)} documents via MCP filesystem server")
