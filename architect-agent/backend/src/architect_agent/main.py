from __future__ import annotations

from pathlib import Path

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from architect_agent.a2a.card import build_agent_card
from architect_agent.a2a.executor import ArchitectAgentExecutor
from architect_agent.api.routes import router as design_router
from architect_agent.config import get_settings


def _spa_index(static_dir: Path) -> Path:
    index = static_dir / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "UI not built. From architect-agent/ui run: npm install && npm run build"
            ),
        )
    return index


def create_app() -> FastAPI:
    settings = get_settings()
    static_dir = settings.static_dir
    static_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Architect Agent",
        description=(
            "Software Factory Architect (BA+Architect). "
            "POST /design starts a LangGraph design session; continue on the shared UI."
        ),
        version="0.1.0",
    )

    app.include_router(design_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "architect-agent"}

    agent_card = build_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=ArchitectAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")

    @app.get("/", include_in_schema=False)
    async def spa_root() -> FileResponse:
        return FileResponse(_spa_index(static_dir))

    @app.get("/sessions/{session_id}", include_in_schema=False)
    async def spa_session(session_id: str) -> FileResponse:
        return FileResponse(_spa_index(static_dir))

    # Fallback for any other non-API client routes (Vite hashed files at root, etc.)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = static_dir / full_path
        if candidate.is_file() and static_dir in candidate.resolve().parents:
            return FileResponse(candidate)
        # Do not swallow API-ish paths that simply 404
        if full_path.startswith(("design", "sessions/", "docs", "openapi", "healthz", ".well-known")):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(_spa_index(static_dir))

    app.state.settings = settings
    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "architect_agent.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
