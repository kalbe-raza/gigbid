"""Live-mode adapter against a mock Upwork MCP server over Streamable HTTP (no real account was available)."""

import socket
import threading
import time

import pytest
import uvicorn

from agent.marketplace_port import MarketplacePort, MarketplaceUnavailable
from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter
from tests.integration import mock_upwork_mcp as mock


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server():
    port = _port()
    srv = uvicorn.Server(uvicorn.Config(mock.app(), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_adapter_maps_tools_through_the_port(server):
    a = UpworkLiveAdapter(server, mock.TOKEN)
    assert isinstance(a, MarketplacePort)
    jobs = await a.search_jobs("fastapi")
    assert jobs["items"][0]["job_id"] == "~01abc"
    job = await a.get_job("~01abc")
    assert job["title"] == "Live job"
    d1 = await a.create_proposal_draft("~01abc", "Hello " * 20, [], 500, "fixed", "idem-1")
    d2 = await a.create_proposal_draft("~01abc", "Hello " * 20, [], 500, "fixed", "idem-1")
    assert d1["proposal_id"] == d2["proposal_id"] and d1["status"] == "draft"
    sub = await a.submit_proposal(d1["proposal_id"], "idem-2")
    assert sub["status"] == "awaiting_platform_confirmation" and sub["connects_spent"] == 0  # never finalised by GigBid


@pytest.mark.anyio
async def test_bad_token_is_degraded_mode(server):
    with pytest.raises(MarketplaceUnavailable):
        await UpworkLiveAdapter(server, "wrong").get_job("~01abc")
