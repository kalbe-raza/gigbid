#!/usr/bin/env bash
# Run the whole stack on this machine and publish it through a free Cloudflare quick tunnel (no account needed).
#
#     scripts/live_demo.sh            # prints https://<random>.trycloudflare.com ; Ctrl+C stops everything
#     NO_PHOENIX_TUNNEL=1 scripts/live_demo.sh   # do not publish the Phoenix UI (trace links then point at localhost)
#     NO_TUNNEL=1 scripts/live_demo.sh           # local only, nothing published (http://localhost:3000)
#
# Used for the demo and grading sessions while no free always-on host can run the container (Hugging Face now needs
# a paid plan for Docker Spaces; see docs/RUNBOOK.md). The tunnel URL changes every time this script starts.
# Services: Postgres, Redis, Qdrant, Phoenix (docker compose), GigSim, the three MCP servers, API, Arq worker and the
# production Next.js build; secrets come from .env as everywhere else.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
LOG=${LOG_DIR:-/tmp/gigbid-live}
mkdir -p "$LOG"
CF_IMAGE=cloudflare/cloudflared:2026.9.3
PIDS=()

if ss -ltn | grep -qE ':(3000|8000|8101|8102|8103) '; then
  echo "a GigBid port (3000, 8000, 8101-8103) is already in use; stop the other GigBid stack first" >&2; exit 1
fi
cleanup() {
  set +e; trap - EXIT INT TERM
  echo; echo "stopping..."
  docker rm -f gigbid-tunnel-web gigbid-tunnel-phoenix >/dev/null 2>&1 || true
  for p in "${PIDS[@]}"; do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null || true; done
  sleep 5  # then force-stop anything still draining connections (SSE streams keep uvicorn waiting)
  for p in "${PIDS[@]}"; do pkill -KILL -P "$p" 2>/dev/null; kill -KILL "$p" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

set -a; . ./.env; set +a
export COOKIE_SECURE=1 HF_HUB_DISABLE_PROGRESS_BARS=1
PY=$ROOT/.venv/bin

tunnel() {  # name local-url -> prints the public URL
  docker rm -f "gigbid-tunnel-$1" >/dev/null 2>&1 || true
  docker run -d --name "gigbid-tunnel-$1" --network host "$CF_IMAGE" tunnel --no-autoupdate --url "$2" >/dev/null
  for _ in $(seq 60); do
    url=$(docker logs "gigbid-tunnel-$1" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1 || true)
    [ -n "$url" ] && { echo "$url"; return 0; }
    sleep 1
  done
  echo "tunnel $1 did not come up; see: docker logs gigbid-tunnel-$1" >&2; return 1
}

echo "starting Postgres, Redis, Qdrant, Phoenix..."
# the dev containers that .env points at (ports 55432, 56379, 56333, 56006); see docs/RUNBOOK.md
docker start ${INFRA_CONTAINERS:-gb-pg gb-redis gb-qdrant gb-phoenix} >/dev/null
sleep 3

if [ -z "${NO_PHOENIX_TUNNEL:-}" ] && [ -z "${NO_TUNNEL:-}" ]; then
  PHX_PORT=$(echo "${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:6006}" | grep -oE '[0-9]+$')
  export PHOENIX_PUBLIC_URL=$(tunnel phoenix "http://localhost:$PHX_PORT")
fi

start() {  # name cmd...
  local name=$1; shift
  "$@" > "$LOG/$name.log" 2>&1 &
  PIDS+=($!)
}
GS_PORT=$(echo "$GIGSIM_BASE_URL" | grep -oE '[0-9]+$')
start gigsim      "$PY/uvicorn" gigsim.app:app --port "$GS_PORT"
start portfolio   "$PY/python" -m mcp_servers.portfolio.server --http
start marketplace "$PY/python" -m mcp_servers.marketplace.server
start workspace   "$PY/python" -m mcp_servers.workspace.server
sleep 4
start api         "$PY/uvicorn" apps.api.main:app --port 8000
start worker      "$PY/arq" worker.main.WorkerSettings
if [ ! -f apps/web/.next/BUILD_ID ]; then
  echo "building the web app once (a few minutes)..."
  (cd apps/web && npm run build > "$LOG/web-build.log" 2>&1)
fi
(cd apps/web && exec ./node_modules/.bin/next start -p 3000 > "$LOG/web.log" 2>&1) &  # exec: this PID is the server
PIDS+=($!)

echo "waiting for the API and the web app..."
for _ in $(seq 90); do
  curl -sf localhost:8000/health >/dev/null 2>&1 && curl -sf -o /dev/null localhost:3000 && break
  sleep 2
done
curl -sf localhost:8000/health >/dev/null || { echo "API did not start; see $LOG/api.log" >&2; exit 1; }

if [ -n "${NO_TUNNEL:-}" ]; then WEB_URL=http://localhost:3000; else WEB_URL=$(tunnel web http://localhost:3000); fi
echo
echo "  GigBid is live at:  $WEB_URL"
[ -n "${PHOENIX_PUBLIC_URL:-}" ] && echo "  Phoenix traces at:  $PHOENIX_PUBLIC_URL"
echo "  reviewer login: the REVIEWER_ACCOUNTS entry in .env    logs: $LOG"
echo "  Ctrl+C stops everything."
wait
