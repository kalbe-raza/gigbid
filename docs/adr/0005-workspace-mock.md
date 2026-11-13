# ADR 0005: Workspace server is a documented local mock
Google Calendar/Gmail need OAuth app verification for a public demo. The Workspace MCP server implements the same three
tools (free slots, tentative holds, email drafts) on Postgres. Agents only see MCP tools, so swapping in Google's APIs
changes `mcp_servers/workspace/server.py` only.
