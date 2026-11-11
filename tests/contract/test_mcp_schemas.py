"""Contract test (A7): MCP tool signatures are snapshotted. An unintended change to a tool's name, input schema,
output schema or annotations fails CI. To accept an intended change: UPDATE_SNAPSHOTS=1 pytest tests/contract"""

import json
import os
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

SNAP = Path(__file__).parent / "snapshots"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _servers():
    from mcp_servers.marketplace.server import mcp as marketplace
    from mcp_servers.portfolio.server import mcp as portfolio
    from mcp_servers.workspace.server import mcp as workspace

    return {"portfolio": portfolio, "marketplace": marketplace, "workspace": workspace}


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["portfolio", "marketplace", "workspace"])
async def test_tool_schema_snapshot(name):
    server = _servers()[name]
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        tools = (await s.list_tools()).tools
    current = sorted(({"name": t.name, "description": t.description, "inputSchema": t.inputSchema,
                       "outputSchema": t.outputSchema,
                       "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None}
                      for t in tools), key=lambda x: x["name"])
    path = SNAP / f"{name}.json"
    if os.getenv("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAP.mkdir(exist_ok=True)
        path.write_text(json.dumps(current, indent=1, sort_keys=True))
    expected = json.loads(path.read_text())
    assert json.loads(json.dumps(current, sort_keys=True)) == expected, (
        f"{name} MCP tool signatures changed; if intended run UPDATE_SNAPSHOTS=1 pytest tests/contract")
