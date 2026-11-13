# ADR 0001: Stack
Python 3.12 + LangGraph 1.x (hand-written StateGraphs), official MCP Python SDK (FastMCP) for our three servers,
`langchain-mcp-adapters` MultiServerMCPClient on the agent side, FastAPI + Arq/Redis, Postgres (app data, LangGraph
checkpointer and store), Qdrant (+ in-memory BM25), fastembed ONNX models on CPU, self-hosted Arize Phoenix for traces,
Next.js 15 + TypeScript with a client generated from FastAPI's OpenAPI schema. Chosen for: official SDKs where they
exist, everything self-hostable for free, one language for agents/servers/evals.
