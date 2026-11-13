# Runbook

## Start
```bash
cp .env.example .env            # fill keys and random secrets
make up                         # docker compose: web, api, worker, 3 MCP servers, GigSim, Postgres, Redis, Qdrant, Phoenix, Ollama
# web http://localhost:3000 · API docs http://localhost:8000/docs · traces http://localhost:6006
```
Local development without containers for the app: `make infra` (Postgres, Redis, Qdrant, Phoenix) then the processes in
`Procfile.dev`. Reviewer accounts are created at startup from `REVIEWER_ACCOUNTS`.

## Deploy (free, public)
Hugging Face Docker Space, single container (`deploy/space/Dockerfile`): nginx on 7860 → web / API (SSE unbuffered) /
Phoenix (`/phoenix`). `HF_TOKEN=... make deploy SPACE=<user>/gigbid` uploads the code and sets secrets as Space
secrets. State is in `/tmp` (free Spaces have no persistent disk): on restart the database is re-created, the reviewer
is re-seeded and the scout re-scans. Free Spaces sleep after 48 h without visits and wake on the next request (cold
start ~1-2 min).

## Scheduling and rate limits
| Thing | Interval | Why |
|---|---|---|
| GigSim saved searches | 15 min (`saved_search_interval_s`) | GigSim releases jobs every 10-30 min; scans fetch only jobs released since the last run (`posted_after`) |
| Upwork live searches | 120 min (`live_interval_s`), ≤ 10 calls/min | the official MCP server is for user-driven use, not monitoring; two-hourly checks of a few saved searches match a person checking their feed, never look like scraping, and keep well under any per-app quota |
| Pipeline sync | 2 min | event feed only (cheap), drives the Kanban and outcome memory |
| GigSim client limit | 60 req/min; we stay at 40 | client-side limiter in the adapter + per-user limiter in the Marketplace MCP server |
| LLMs (Groq free tier) | 8k tokens/min, 200k tokens/day per model | client-side token pacing per model; fallback model on exhaustion |

Jobs are processed idempotently: an analysis thread id is `(user, job)`; a worker restart never re-analyses a job that
already has a checkpoint, and Arq de-duplicates queued job ids.

## Degraded mode (viva question 1: "kill the marketplace MCP server")
1. `docker compose stop mcp-marketplace` (or kill the process).
2. Next MCP call raises `MCPUnavailable` (`agent/mcp_client.py`, connection errors after bounded retries).
3. The worker re-queues the job with backoff (`worker/main.py: analyse_job / scout_scan`) and sets the Redis flag
   `gigbid:degraded:marketplace`; jobs show "queued".
4. `GET /api/health` reports `marketplace: false`; the UI shows a red "Marketplace unavailable - new work is queued and
   will resume automatically" banner (`apps/web/src/components/DegradedBanner.tsx`).
5. Restart the server; the next retry succeeds, the flag is cleared, the banner disappears.

## Crash between approval and submission (viva question 2)
1. Open a job awaiting approval in the studio; stop the worker (`docker compose stop worker`).
2. Click Approve: the API signs the dry-run text, stores the decision on the run (`runs.pending_resume`), status
   `resuming`, and enqueues `resume_thread`.
3. Start the worker: its startup sweep re-enqueues every `resuming`/`running` run; `resume()` continues from the Postgres
   checkpoint; `submit` uses idempotency keys `(thread, draft version)`.
4. Proof of exactly once: `GET /admin/outcomes` on GigSim (or the audit log) shows one submission and one Connects
   spend for that proposal, even if `resume_thread` ran twice.

## Replay
Run viewer → Checkpoint history → "replay" re-executes from any checkpoint (`POST /api/runs/{id}/replay/{checkpoint}`).

## Secrets
Only from environment variables (`.env` locally, Space secrets in production). `.env` is git-ignored; CI scans tracked
files for key patterns. OAuth tokens and marketplace credentials are Fernet-encrypted per user; logs and traces pass
through a PII/secret mask.

## Recovery checklist
- Dead-letter runs (after 4 failures) appear in Agent runs with the error; fix the cause, then "Analyse" again.
- `needs_attention` threads (step/token budget, not enough Connects) show the reason in the studio.
- LLM quota exhausted: nodes fall back to the OpenRouter free model; the analyst/critic have deterministic fallbacks.
