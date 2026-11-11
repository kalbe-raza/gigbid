"""Portfolio MCP server through an in-memory MCP client (official SDK test transport)."""

import os

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

os.environ["PORTFOLIO_CORPUS"] = "fullstack"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_tools_resources_prompts():
    from mcp_servers.portfolio.server import mcp

    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(tools) == {"search_evidence", "get_case_study", "list_skills"}
        assert all(t.annotations.readOnlyHint for t in tools.values())
        r = await s.call_tool("search_evidence", {"requirement": "background task queue with Celery", "k": 5})
        ids = [i["id"] for i in r.structuredContent["items"]]
        assert any(i in ids for i in ("repo:trackline", "repo:tutorhub")), ids  # implied skill found in code
        item = r.structuredContent["items"][0]
        assert {"id", "type", "source", "date", "outcome", "snippet"} <= set(item)
        cs = await s.call_tool("get_case_study", {"case_id": "trackline"})
        assert cs.structuredContent["project"] == "trackline"
        missing = await s.call_tool("get_case_study", {"case_id": "does-not-exist"})
        assert missing.isError
        templates = [t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates]
        assert "portfolio://case/{case_id}" in templates and "portfolio://proposal/{proposal_id}" in templates
        prop = await s.read_resource("portfolio://proposal/pp-fu-000")
        assert "outcome" in prop.contents[0].text
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        assert "answer_screening_question" in prompts


@pytest.mark.anyio
async def test_filters_by_type():
    from mcp_servers.portfolio.server import mcp

    async with create_connected_server_and_client_session(mcp._mcp_server) as s:
        r = await s.call_tool("search_evidence", {"requirement": "Stripe subscription billing", "k": 3,
                                                  "filters": {"types": ["case_study"]}})
        assert r.structuredContent["items"] and all(i["type"] == "case_study" for i in r.structuredContent["items"])
