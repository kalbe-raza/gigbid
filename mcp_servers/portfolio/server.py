"""Portfolio MCP server (A1, read-only). Built with the official MCP Python SDK (FastMCP).

Tools      search_evidence(requirement, k, filters) | get_case_study(id) | list_skills()
Resources  portfolio://case/{case_id} | portfolio://proposal/{proposal_id} | portfolio://repo/{repo} | portfolio://cv
Prompts    answer_screening_question | evidence_for_job | portfolio_gaps

Standalone (Claude Desktop / Cursor), pointing at your own folder:
    PORTFOLIO_DIR=~/my-portfolio uvx --from "git+https://github.com/kalbe-raza/gigbid" gigbid-portfolio-mcp
Inside GigBid it runs over streamable HTTP with bearer auth:  python -m mcp_servers.portfolio.server --http
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuth, audit, identity
from mcp_servers.portfolio import prompts as P
from mcp_servers.portfolio import resources as R
from mcp_servers.portfolio import tools as T
from mcp_servers.portfolio.corpus import default_corpus, reindex

SERVER = "portfolio"
mcp = FastMCP("gigbid-portfolio", instructions=(
    "Read-only access to one freelancer's portfolio: CV, case studies, repositories, client reviews and past "
    "proposals with outcomes. Cite evidence ids (e.g. case:helpdesk-rag) for every claim you make from it."),
    host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("PORTFOLIO_MCP_PORT", "8101")),
    stateless_http=True, json_response=True)

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _corpus(ctx: Context | None) -> tuple[str, str, str | None]:
    if ctx is None:
        return default_corpus(), "local", None
    user, trace, corpus = identity(ctx)
    return corpus or default_corpus(), user, trace


@mcp.tool(annotations=RO)
async def search_evidence(ctx: Context, requirement: Annotated[str, Field(min_length=3, max_length=500)],
                          k: Annotated[int, Field(ge=1, le=20)] = 5,
                          filters: T.EvidenceFilters | None = None) -> T.EvidenceResult:
    """Find portfolio evidence (case studies, repos/code, reviews, CV) that proves one requirement. Each item has
    an id to cite, its type, source, date and, for proposals, the outcome."""
    corpus, user, trace = _corpus(ctx)
    res = T.search_evidence(corpus, requirement, k, filters)
    await audit(SERVER, user, "search_evidence", {"requirement": requirement, "k": k}, {"ids": [i.id for i in res.items]},
                trace, True)
    return res


@mcp.tool(annotations=RO)
async def get_case_study(ctx: Context, case_id: Annotated[str, Field(min_length=2, max_length=100)]) -> T.CaseStudy:
    """Full text of one case study (id like `case:helpdesk-rag` or just `helpdesk-rag`) plus related evidence ids."""
    corpus, user, trace = _corpus(ctx)
    try:
        res = T.get_case_study(corpus, case_id)
    except KeyError as e:
        raise ToolError(str(e)) from e
    await audit(SERVER, user, "get_case_study", {"case_id": case_id}, {"id": res.id}, trace, True)
    return res


@mcp.tool(annotations=RO)
async def list_skills(ctx: Context) -> T.SkillList:
    """Every skill the portfolio has evidence for, with how many evidence items and which projects show it."""
    corpus, user, trace = _corpus(ctx)
    return T.list_skills(corpus)


# ------------------------------------------------------------------------------------------ resources
@mcp.resource("portfolio://case/{case_id}", mime_type="text/markdown", title="Case study")
def case_resource(case_id: str) -> str:
    return R.case(default_corpus(), case_id)


@mcp.resource("portfolio://proposal/{proposal_id}", mime_type="text/markdown", title="Past proposal with outcome")
def proposal_resource(proposal_id: str) -> str:
    return R.proposal(default_corpus(), proposal_id)


@mcp.resource("portfolio://repo/{repo}", mime_type="text/markdown", title="Repository README and file list")
def repo_resource(repo: str) -> str:
    return R.repo(default_corpus(), repo)


@mcp.resource("portfolio://cv", mime_type="text/plain", title="CV text")
def cv_resource() -> str:
    return R.cv(default_corpus())


# -------------------------------------------------------------------------------------------- prompts
@mcp.prompt(title="Answer a screening question from my portfolio")
def answer_screening_question(question: str, job_title: str = "") -> str:
    return P.answer_screening_question(question, job_title)


@mcp.prompt(title="Find my strongest evidence for a job post")
def evidence_for_job(job_post: str) -> str:
    return P.evidence_for_job(job_post)


@mcp.prompt(title="What is my portfolio missing for this kind of work?")
def portfolio_gaps(target_role: str) -> str:
    return P.portfolio_gaps(target_role)


# --------------------------------------------------------------------------------------- admin routes
@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": SERVER})


@mcp.custom_route("/admin/reindex", methods=["POST"])
async def admin_reindex(request: Request) -> JSONResponse:
    """Incremental re-index after an upload (called by the API; protected by the same bearer middleware)."""
    body = await request.json()
    from pathlib import Path

    from rag.ingest.loaders import load_portfolio

    corpus = body["corpus"]
    from mcp_servers.portfolio.corpus import corpus_dir

    changed = [d for d in load_portfolio(Path(corpus_dir(corpus)), corpus) if d.doc_id in set(body.get("doc_ids", []))] \
        if body.get("doc_ids") else load_portfolio(Path(corpus_dir(corpus)), corpus)
    n = reindex(corpus, changed=changed, removed=body.get("removed") or None)
    return JSONResponse({"corpus": corpus, "chunks_upserted": n, "documents": len(changed)})


def app():
    a = mcp.streamable_http_app()
    a.add_middleware(BearerAuth, env_var="MCP_SERVICE_TOKENS")
    return a


def main() -> None:
    if "--http" in sys.argv:
        import uvicorn

        from agent.tracing import init_tracing

        init_tracing("mcp-portfolio")
        uvicorn.run(app(), host=mcp.settings.host, port=mcp.settings.port, log_level="warning")
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
