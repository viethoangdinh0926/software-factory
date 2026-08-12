# Architect Agent

```
architect-agent/
  backend/          # Python A2A FastAPI + LangGraph service
  ui/               # TypeScript React (Vite) SPA
  skills/           # grill-me skill used by the interview node
  .env              # LLM + host config
```

After `ui` is built, static files land in `backend/src/architect_agent/static/` and are served at the service public base URL.

## Deploy (from scratch)

```bash
cd architect-agent
make deploy          # env + install + build UI + run
# or: make
```

Other targets: `make install`, `make build`, `make run`, `make clean`, `make help`.

## Develop UI

```bash
cd ui
npm install
npm run dev          # http://127.0.0.1:5173 (proxies API to :8080)
```

## Build UI into backend

```bash
cd ui
npm run build
```

## Run backend

```bash
cd backend
uv sync
uv run architect-agent
```

Open `http://127.0.0.1:8080/` for the React UI, or `http://127.0.0.1:8080/docs` for API docs.

### Start a design session (API)

```bash
curl -s -X POST http://127.0.0.1:8080/design \
  -F 'markdown=# Inventory Tracker

Vague idea: track warehouse stock.'
```

Returns `design_session_id` and `ui_url` (`/sessions/{id}`).

### LLM (`.env` at architect-agent root)

| Variable | Meaning |
|----------|---------|
| `LLM_PROVIDER` | `stub`, `openai`, `anthropic`, or `ollama` |
| `LLM_MODEL` | Model id |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Cloud keys |
| `OLLAMA_BASE_URL` | Local Ollama endpoint |

### A2A

- Agent card: `GET /.well-known/agent-card.json`
- JSON-RPC: `POST /`
- On each **Approve & send design**, the architect writes a design-package markdown and delivers it to the Software System Manager via A2A (`SYSTEM_MANAGER_AGENT_URL`). If unset/unreachable, packages are queued under `backend/data/handoffs/`.
