# Engineer Agent

```
engineer-agent/
  backend/          # Python A2A FastAPI service
  ui/               # TypeScript React (Vite) SPA
  skills/           # engineer fleet skill
  .env              # LLM + host config
```

After `ui` is built, static files land in `backend/src/engineer_agent/static/` and are served at `http://127.0.0.1:8091`.

## Role

Receives orchestrator **plan specs** (A2A markdown) keyed by architect `design_session_id`
and `microservice_id`. One **sub-engineer** per `(design_session_id, microservice_id)`
owns that service's offered API/protocol and an **execution plan**. A new spec version
stops in-flight coding, compares with the previous spec, and drafts a new plan. Approve
the plan in the UI to start a long-running coding loop. The sub-engineer follows the
plan one item at a time in priority order, adds tests, and only moves on when all
workspace tests pass. Peer contracts are settled before cross-service items. If git
pull fails, a peer contract cannot be settled, or tests fail, the sub-engineer pauses,
streams the issue to the UI, and chats for instructions. **Approve to continue** follows
those instructions and retries. Pause to revise the plan is separate; execute again to
transition from current progress. Code is shipped to git only after the entire plan
completes. The sub-engineer pulls the fleet repo first and works in a private folder at
the git root named after the microservice.

When the orchestrator relationship map says this service **initiates** toward another
core microservice, the sub-engineer consults that peer's offered API. If it needs more
or less data, it asks the peer sub-engineer to update the peer API.

The orchestrator no longer dictates communication schemes. Protocols live here.

## Deploy

```bash
cd engineer-agent
make deploy          # env + install + build UI + run
```

Other targets: `make install`, `make build`, `make run`, `make clean`.

## Develop UI

```bash
cd ui
npm install
npm run dev          # http://127.0.0.1:5175 (proxies API to :8091)
```

## Smoke (stub LLM)

```bash
cd backend
LLM_PROVIDER=stub uv run python scripts/test_engineer_flow.py
```

## A2A

- Agent card: `GET /.well-known/agent-card.json`
- JSON-RPC: `POST /`
- HTTP ingest (testing): `POST /ingest` form field `markdown`

Orchestrator delivers plan specs to `ENGINEER_AGENT_URL=http://127.0.0.1:8091`.
