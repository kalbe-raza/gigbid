# Marketplace MCP server (streamable HTTP)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    FASTEMBED_CACHE=/models HF_HUB_DISABLE_PROGRESS_BARS=1 PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agent agent
COPY rag rag
COPY mcp_servers mcp_servers
COPY apps/__init__.py apps/__init__.py
COPY apps/api apps/api
COPY worker worker
COPY gigsim gigsim
COPY evals/__init__.py evals/__init__.py
COPY evals/runners evals/runners
COPY data/personas data/personas
COPY data/policy_pack data/policy_pack
COPY data/market/market_stats.json data/market/market_stats.json
RUN mkdir -p /data/uploads /app/data/processed && chown -R 1000 /data /app/data/processed
USER 1000
EXPOSE 8102
CMD ["python", "-m", "mcp_servers.marketplace.server"]
