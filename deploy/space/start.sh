#!/usr/bin/env bash
# Boot script for the Hugging Face Space. Secrets arrive as environment variables (Space settings), never in files.
set -euo pipefail
export STATE=/tmp/gigbid
mkdir -p "$STATE"/{pg,redis,qdrant,phoenix,uploads,gigsim,nginx,logs}
PGBIN=$(ls -d /usr/lib/postgresql/*/bin | head -1)
if [ ! -f "$STATE/pg/PG_VERSION" ]; then
  "$PGBIN/initdb" -D "$STATE/pg" -U gigbid --auth=trust >/dev/null
fi
export PGBIN
export DATABASE_URL="postgresql://gigbid@127.0.0.1:5432/postgres"
export REDIS_URL="redis://127.0.0.1:6379/0"
export QDRANT_URL="" QDRANT_PATH="$STATE/qdrant"
export GIGSIM_BASE_URL="http://127.0.0.1:8081" GIGSIM_DB="$STATE/gigsim/gigsim.db"
export PORTFOLIO_MCP_URL="http://127.0.0.1:8101/mcp" MARKETPLACE_MCP_URL="http://127.0.0.1:8102/mcp" WORKSPACE_MCP_URL="http://127.0.0.1:8103/mcp"
export PHOENIX_COLLECTOR_ENDPOINT="http://127.0.0.1:6006" PHOENIX_WORKING_DIR="$STATE/phoenix" PHOENIX_HOST_ROOT_PATH="/phoenix"
export PHOENIX_PUBLIC_URL="${PHOENIX_PUBLIC_URL:-https://${SPACE_HOST:-localhost:7860}/phoenix}"
export UPLOADS_DIR="$STATE/uploads" AUDIT_FALLBACK_PATH="$STATE/audit.jsonl"
export MCP_SERVICE_TOKENS="${MCP_SERVICE_TOKEN}"
export GIGSIM_API_TOKEN="${GIGSIM_API_TOKEN:-gs-dev-token}" GIGSIM_DEMO_TOKEN="${GIGSIM_DEMO_TOKEN:-gs-dev-token}"
export COOKIE_SECURE=1 CORS_ORIGINS="https://${SPACE_HOST:-localhost}" WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-2}"
export GIGSIM_RESPONSE_DELAY_SECONDS="${GIGSIM_RESPONSE_DELAY_SECONDS:-120}"
# conservative live stream on the free tier: ~2 new jobs per hour after an initial batch
export GIGSIM_RELEASE_SECONDS="${GIGSIM_RELEASE_SECONDS:-1800}" GIGSIM_RELEASE_BATCH="${GIGSIM_RELEASE_BATCH:-1}" GIGSIM_INITIAL_LIVE="${GIGSIM_INITIAL_LIVE:-12}"
exec /usr/bin/supervisord -c /etc/gigbid/supervisord.conf
