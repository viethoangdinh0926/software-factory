# Software Factory

A local multi-agent design factory. The **Architect** turns a vague idea into an approved system design; the **Orchestrator** turns that design into engineer-ready plan specs. Both agents expose a React UI, a FastAPI BFF, and an A2A JSON-RPC surface. They share the architect `design_session_id` as the workflow key.

| Agent | UI / API | Default URL |
| --- | --- | --- |
| Architect | design interview, LLD/HLD, market eval | http://127.0.0.1:8080/ |
| Orchestrator | stack + API planning, engineer handoff | http://127.0.0.1:8090/ |

The Engineer agent is not in this repo yet. Orchestrator queues plan specs under `orchestrator-agent/backend/data/plan_specs/` (or sends them to `ENGINEER_AGENT_URL` when set).

## Architecture

```mermaid
flowchart TB
  User["User"]

  subgraph Factory["Software Factory"]
    direction TB

    subgraph Architect["Architect Agent :8080"]
      direction TB
      AUI["React SPA<br/>Home · Session"]
      ABFF["FastAPI BFF<br/>POST /design · /api/sessions<br/>static UI · /healthz"]
      AA2A["A2A<br/>agent card · JSON-RPC · executor"]
      ASess["Session store"]
      AGraph["LangGraph + SQLite checkpointer"]
      ANodes["phase0 classify<br/>LLD steps 1–3<br/>HLD steps 1–6<br/>market evaluation"]
      ASkill["Skills<br/>principal-architect · grill-me"]
      ALLM["LLM adapter"]
      ASearch["Web search<br/>market research"]
      AData["data/<br/>sessions · checkpoints.sqlite<br/>handoffs/ queue"]
      APeer["Orchestrator A2A client"]

      AUI --> ABFF
      AA2A --> ABFF
      ABFF --> ASess
      ASess --> AGraph
      AGraph --> ANodes
      ANodes --> ASkill
      ANodes --> ALLM
      ANodes --> ASearch
      AGraph --> AData
      ASess --> APeer
    end

    subgraph Orchestrator["Orchestrator Agent :8090"]
      direction TB
      OUI["React SPA<br/>Home · Session tiles"]
      OBFF["FastAPI BFF<br/>POST /ingest · /api/sessions<br/>static UI · /healthz"]
      OA2A["A2A<br/>agent card · JSON-RPC · executor"]
      OSess["Session store"]
      OGraph["LangGraph + SQLite checkpointer"]
      ONodes["ingest · classify · handle update<br/>extract services · prime all tiles<br/>API type · API design · stack<br/>emit plan · wait"]
      OSkill["Skill<br/>orchestrator"]
      OLLM["LLM adapter"]
      OSearch["Web search"]
      OParse["Design-package parser"]
      OData["data/<br/>sessions · checkpoints.sqlite<br/>plan_specs/ queue"]
      OPeer["Engineer A2A client"]

      OUI --> OBFF
      OA2A --> OBFF
      OBFF --> OSess
      OSess --> OGraph
      OGraph --> ONodes
      OGraph --> OParse
      ONodes --> OSkill
      ONodes --> OLLM
      ONodes --> OSearch
      OGraph --> OData
      OSess --> OPeer
    end
  end

  Engineer["Engineer Agent<br/>not in repo yet"]

  User -->|"chat / approve"| AUI
  User -->|"chat / approve per service"| OUI
  APeer -->|"design package markdown<br/>keyed by design_session_id"| OA2A
  OPeer -->|"plan spec markdown"| Engineer
```

Same `design_session_id` on both UIs. Architect handoff is A2A to `ORCHESTRATOR_AGENT_URL` (default `:8090`); if that peer is down, packages land in `architect-agent/backend/data/handoffs/`.

## Design-to-plan flow

```mermaid
flowchart LR
  Idea["Business spec"] --> P0["Phase 0<br/>LLD vs HLD"]
  P0 --> Track["LLD 1–3 or HLD 1–6<br/>living spec + trade-off ledger"]
  Track --> Approve["Approve design version"]
  Approve --> Market["Market evaluation"]
  Market --> Pkg["Design package"]
  Pkg --> Class{"Topology"}

  Class -->|"stand-alone"| Stack["Stack interview"]
  Stack --> PlanA["Plan spec"]
  PlanA --> Lock["Discussion locked<br/>until next architect package"]

  Class -->|"distributed"| Tiles["Prime all microservices<br/>one UI tile each"]
  Tiles --> PerSvc["Per tile: API type → API design → stack"]
  PerSvc --> PlanB["Plan spec per service"]
  PlanB --> Open["Tile stays open<br/>revise API anytime"]

  PlanA --> Eng["Engineer queue"]
  PlanB --> Eng
```

**Distributed:** every live microservice is discussed at once. Reopen `/sessions/{design_session_id}` on the orchestrator to change an API and hand off again.

**Stand-alone:** after the first engineer handoff, orchestrator chat/approve are locked until the architect sends another package. The session remains readable in the UI.

## Repository

```
software-factory/
  Makefile                 # deploy / teardown the whole factory
  architect-agent/         # Principal Architect — see architect-agent/README.md
    backend/               # FastAPI + A2A + LangGraph
    ui/                    # Vite React SPA → backend static/
    skills/                # principal-architect, grill-me
  orchestrator-agent/      # Delivery planner — see orchestrator-agent/README.md
    backend/
    ui/
    skills/                # orchestrator
```

## Deploy

From this directory:

```bash
make deploy      # .env, install, build UIs, start both agents
make teardown    # stop both agents
make status      # pid + /healthz
make logs        # tail .run/*.log
```

Copy each agent's `.env.example` to `.env` and set `LLM_PROVIDER` / keys there. Set `ORCHESTRATOR_AGENT_URL` in `architect-agent/.env` to point to the orchestrator (default `http://127.0.0.1:8090`).

Single-agent foreground run: `make -C architect-agent deploy` or `make -C orchestrator-agent deploy`.
