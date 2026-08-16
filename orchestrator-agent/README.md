# Orchestrator Agent

```
orchestrator-agent/
  backend/          # Python A2A FastAPI + LangGraph service
  ui/               # TypeScript React (Vite) SPA
  skills/           # orchestrator planning skill
  .env              # LLM + host config
```

After `ui` is built, static files land in `backend/src/orchestrator_agent/static/` and are served at `http://127.0.0.1:8090`.

## Role

Receives architect **design packages** (A2A markdown) keyed by the architect `design_session_id`. Classifies stand-alone vs distributed, plans tech stacks (and per-microservice APIs in **parallel tiles**), then queues plan specs for the Engineer agent (`ENGINEER_AGENT_URL`). If that URL is unset, specs are written under `backend/data/plan_specs/`.

Distributed workflows stay open: reopen `/sessions/{design_session_id}` anytime to revise a service API and hand off an update. Stand-alone discussion locks after the first engineer handoff until the architect sends a new package.

## Deploy

```bash
cd orchestrator-agent
make deploy          # env + install + build UI + run
```

Other targets: `make install`, `make build`, `make run`, `make clean`.

## Develop UI

```bash
cd ui
npm install
npm run dev          # http://127.0.0.1:5174 (proxies API to :8090)
```

## Smoke (stub LLM)

```bash
cd backend
LLM_PROVIDER=stub uv run python scripts/test_orchestrator_flow.py
```

## A2A

- Agent card: `GET /.well-known/agent-card.json`
- JSON-RPC: `POST /`
- HTTP ingest (testing): `POST /ingest` form field `markdown`

Architect delivers packages to `ORCHESTRATOR_AGENT_URL=http://127.0.0.1:8090`.
