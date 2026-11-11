# make dev | test | eval | seed | demo | lint   (see docs/RUNBOOK.md)
.PHONY: dev infra test lint eval seed demo web e2e openapi up down deploy freeze-gigsim
PY ?= uv run

infra:            ## dev dependencies only: Postgres, Redis, Qdrant, Phoenix
	docker compose -f infra/docker-compose.yml --env-file .env up -d postgres redis qdrant phoenix

dev: infra        ## GigSim + MCP servers + API + worker + web, with reload
	$(PY) honcho start -f Procfile.dev 2>/dev/null || (echo "pip install honcho, or run the commands in Procfile.dev in separate shells" && exit 1)

up:               ## everything in containers (R10)
	docker compose -f infra/docker-compose.yml --env-file .env up --build

down:
	docker compose -f infra/docker-compose.yml --env-file .env down

seed:             ## regenerate market data, personas, dev set and GigSim seed from data_raw/ (deterministic)
	$(PY) python scripts/prepare_market_data.py --raw ../../data_raw --out data/market
	$(PY) python scripts/generate_personas.py
	$(PY) python scripts/build_dev_set.py

lint:
	$(PY) ruff check .
	cd apps/web && npx tsc --noEmit

test:             ## unit, MCP servers (in-memory client), contract snapshots, live-adapter mock
	$(PY) pytest -q tests/unit mcp_servers tests/contract tests/integration/test_upwork_adapter.py
	$(PY) python -m scripts.ci_checks

e2e:
	cd apps/web && npx playwright test

eval:             ## retrieval + decisions + GigSim outcome run; writes evals/results/*.json
	$(PY) python -m evals.runners.rag_eval
	$(PY) python -m evals.runners.decision_eval --risk-llm ambiguous
	RAG_VARIANT=best MODEL_PROFILE=open_b $(PY) python -m evals.runners.gigsim_outcomes --per-persona 6 --tag best_openb
	$(PY) python -m evals.runners.budget_report
	$(PY) python -m evals.runners.results_table

demo:             ## one job, end to end, stops at the human-approval interrupt then approves
	$(PY) python -m scripts.smoke_bid --persona fullstack --approve

openapi:          ## regenerate the typed web client from FastAPI
	$(PY) python -m scripts.export_openapi apps/web/openapi.json && cd apps/web && npm run gen:api

deploy:           ## Hugging Face Space (needs HF_TOKEN and SPACE=<user>/gigbid)
	$(PY) python -m scripts.deploy_space --space $(SPACE)

freeze-gigsim:
	$(PY) python -m scripts.ci_checks --freeze-gigsim
