from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from orchestrator_agent.config import get_settings
from orchestrator_agent.sessions import get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["orchestrator"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    service_id: str | None = None


class ApproveRequest(BaseModel):
    service_id: str | None = None


class IngestResponse(BaseModel):
    design_session_id: str
    ui_url: str
    topology: str
    design_version: int
    phase: str
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
        logger.exception("Failed to ingest design package")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    settings = get_settings()
    last = session.messages[-1]["content"] if session.messages else None
    return IngestResponse(
        design_session_id=session.design_session_id,
        ui_url=f"{settings.public_base_url}/sessions/{session.design_session_id}",
        topology=session.topology,
        design_version=session.design_version,
        phase=session.phase,
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
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
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
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str) -> dict:
    try:
        session = get_store().end_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/retry-ingest")
async def retry_ingest(session_id: str) -> dict:
    try:
        session = get_store().get(session_id)
        # Remove error messages from the session
        session.messages = [
            msg for msg in session.messages 
            if not (msg.get("role") == "system" and "encountered an error" in msg.get("content", ""))
        ]
        # Re-run the ingest process with the saved package using resume
        session = get_store().resume(session_id, "ingest", session.package_markdown)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to retry ingest for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.get("/api/sessions/{session_id}/download/plan")
async def download_plan(session_id: str) -> PlainTextResponse:
    try:
        session = get_store().get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    text = session.plan_spec
    if not text and session.active_service_id:
        for svc in session.services:
            if str(svc.get("microservice_id")) == session.active_service_id:
                text = str(svc.get("plan_spec") or "")
                break
    if not text:
        raise HTTPException(status_code=400, detail="No plan spec has been approved yet.")
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plan-spec-{session_id}.md"'},
    )
