# All-in-one image for the free Hugging Face Docker Space (single container, port 7860, user 1000).
# Same code as infra/docker-compose.yml; differences: one container under supervisord, nginx in front, Qdrant in
# embedded (on-disk) mode inside the Portfolio MCP server, state in /tmp (re-seeded on restart). See docs/RUNBOOK.md.

FROM node:22-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
ENV NEXT_TELEMETRY_DISABLED=1 API_URL=http://127.0.0.1:8000
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    FASTEMBED_CACHE=/models HF_HUB_DISABLE_PROGRESS_BARS=1 PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends postgresql redis-server nginx supervisor curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node
RUN pip install --no-cache-dir uv==0.13.7 && python -m venv /opt/phoenix \
    && /opt/phoenix/bin/pip install --no-cache-dir arize-phoenix==11.38.0
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models'); TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/models')"
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
COPY --from=web /web/.next/standalone /web
COPY --from=web /web/.next/static /web/.next/static
COPY --from=web /web/public /web/public
COPY deploy/space/supervisord.conf /etc/gigbid/supervisord.conf
COPY deploy/space/nginx.conf /etc/gigbid/nginx.conf
COPY deploy/space/start.sh /start.sh
RUN chmod +x /start.sh && chown -R 1000:1000 /app /models /web
USER 1000
ENV HOME=/tmp
EXPOSE 7860
CMD ["/start.sh"]
