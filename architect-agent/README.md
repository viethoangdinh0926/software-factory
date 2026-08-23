# Architect Agent

```
architect-agent/
  backend/          # Python A2A FastAPI + LangGraph service
  ui/               # TypeScript React (Vite) SPA
  skills/           # principal-architect (+ grill-me interview technique)
  .env              # LLM + host config
```

After `ui` is built, static files land in `backend/src/architect_agent/static/` and are served at the service public base URL.

## Design flow (Principal Architect)

1. **Phase 0** — classify **LLD** (single-process) vs **HLD** (distributed).
2. **LLD** steps 1–3 or **HLD** steps 1–6 with a living spec, trade-off ledger, and step artifacts. Confirm each step before the next; no skipping ahead. If a change belongs to an earlier stage, the session rewinds there and walks forward again, keeping later-step artifacts and patching them only where that change requires it.
3. **Approve & send design** (design-version approve) → **market evaluation** (fresh report + grade) → continue → handoff to Orchestrator **only if the package changed** since the last delivery.
4. After every handoff attempt (success or failure), and after a skipped duplicate, a **new round starts at Phase 0**. Ask whether the spec should change, then walk the track again from scope.
5. Intermediate step advances (1–5) do **not** trigger market research.

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

### Smoke (stub LLM)

```bash
cd backend
LLM_PROVIDER=stub uv run python scripts/test_principal_architect_flow.py
```

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
- On each **Continue after market evaluation** (after design-version approve), the architect writes a design-package markdown and delivers it to the Orchestrator via A2A (`ORCHESTRATOR_AGENT_URL`, default `:8090`). If unset/unreachable, packages are queued under `backend/data/handoffs/`. The Orchestrator lives in sibling package `orchestrator-agent/` and reuses the same `design_session_id`.
