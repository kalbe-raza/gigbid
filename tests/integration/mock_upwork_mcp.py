"""A stand-in for the official Upwork MCP server (tool names/shapes are our assumption; see docs/TOOLS.md).
Streamable HTTP + bearer auth, so UpworkLiveAdapter is exercised over the same transport it uses in production."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

TOKEN = "upwork-test-access-token"
mock = FastMCP("mock-upwork", stateless_http=True, json_response=True)
DRAFTS: dict[str, dict] = {}


@mock.tool()
def search_jobs(query: str = "", limit: int = 10) -> dict:
    return {"jobs": [{"job_id": "~01abc", "title": f"{query} job", "skills": ["Python"], "client_id": "c1"}][:limit]}


@mock.tool()
def get_job_details(job_id: str) -> dict:
    return {"job_id": job_id, "title": "Live job", "description": "Build an API", "skills": ["Python"], "budget": 500}


@mock.tool()
def create_proposal(job_id: str, cover_letter: str, amount: float, rate_type: str, answers: list[str] | None = None,
                    client_reference: str = "") -> dict:
    d = DRAFTS.setdefault(client_reference, {"draft_id": f"draft-{len(DRAFTS) + 1}", "job_id": job_id})
    return d


class Auth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        return await call_next(request)


def app():
    a = mock.streamable_http_app()
    a.add_middleware(Auth)
    return a
