from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException
from pydantic import BaseModel, Field

from engineer_agent.config import get_settings
from engineer_agent.sessions import get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["engineer"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    service_id: str | None = None


class ApproveRequest(BaseModel):
    service_id: str | None = None


class PauseRequest(BaseModel):
    service_id: str | None = None


class ExecuteRequest(BaseModel):
    service_id: str | None = None


class GitIngestRequest(BaseModel):
    design_session_id: str = Field(min_length=1)
    git_repo_url: str = Field(min_length=1)
    ssh_private_key: str = Field(min_length=1)


class IngestResponse(BaseModel):
    design_session_id: str
    ui_url: str
    design_version: int
    sub_agent_count: int
    message: str | None = None


@router.post("/ingest", response_model=IngestResponse)
async def ingest_package(markdown: str | None = Form(default=None)) -> IngestResponse:
    if not markdown or not markdown.strip():
        raise HTTPException(status_code=400, detail="Provide form field `markdown`.")
    try:
        session = get_store().ingest(markdown.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to ingest plan spec")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    settings = get_settings()
    live = [s for s in session.sub_agents if s.get("status") != "suspended"]
    last = None
    if live:
        msgs = live[-1].get("messages") or []
        if msgs:
            last = msgs[-1].get("content")
    return IngestResponse(
        design_session_id=session.design_session_id,
        ui_url=f"{settings.public_base_url}/sessions/{session.design_session_id}",
        design_version=session.design_version,
        sub_agent_count=len(live),
        message=last,
    )


@router.get("/api/sessions")
async def list_sessions() -> dict:
    return {"sessions": get_store().list_sessions()}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    try:
        return get_store().get(session_id).to_public()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc


@router.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest) -> dict:
    try:
        session = get_store().chat(session_id, body.message, service_id=body.service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session or sub-engineer") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/approve")
async def approve(session_id: str, body: ApproveRequest | None = None) -> dict:
    service_id = body.service_id if body else None
    try:
        session = get_store().approve(session_id, service_id=service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session or sub-engineer") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/pause")
async def pause(session_id: str, body: PauseRequest | None = None) -> dict:
    service_id = body.service_id if body else None
    try:
        session = get_store().pause(session_id, service_id=service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session or sub-engineer") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/execute")
async def execute(session_id: str, body: ExecuteRequest | None = None) -> dict:
    service_id = body.service_id if body else None
    try:
        session = get_store().execute(session_id, service_id=service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session or sub-engineer") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/git")
async def ingest_git(body: GitIngestRequest) -> dict:
    try:
        session = get_store().ingest_git(
            design_session_id=body.design_session_id,
            git_repo_url=body.git_repo_url,
            ssh_private_key=body.ssh_private_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to store git access")
        raise HTTPException(status_code=500, detail="Could not store git access.") from exc
    return {
        "ok": True,
        "design_session_id": session.design_session_id,
        "message": "Engineer stored git access for this design session.",
        "git_key_configured": True,
        "git_repo_url": session.git_repo_url,
    }
