from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from orchestrator_agent.config import get_settings
from orchestrator_agent.git_access import GitAccessError
from orchestrator_agent.session_presence import SessionBusyError
from orchestrator_agent.sessions import get_store
from orchestrator_agent.workflow import plan_package_markdown

logger = logging.getLogger(__name__)
router = APIRouter(tags=["orchestrator"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    service_id: str | None = None


class ApproveRequest(BaseModel):
    service_id: str | None = None


class GitSaveRequest(BaseModel):
    git_repo_url: str = Field(min_length=1)
    ssh_private_key: str | None = None


class IngestResponse(BaseModel):
    design_session_id: str
    ui_url: str
    topology: str
    design_version: int
    phase: str
    message: str | None = None


class PresenceRequest(BaseModel):
    holder_id: str = Field(min_length=1)


def _holder(x_session_holder: str | None) -> str | None:
    value = (x_session_holder or "").strip()
    return value or None


def _public(session, holder: str | None = None) -> dict:
    data = session.to_public()
    data["interaction"] = get_store().presence.snapshot(session.design_session_id, holder)
    return data


def _require_interaction(session_id: str, holder: str | None) -> None:
    try:
        get_store().presence.require(session_id, holder)
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=423,
            detail="This session is open in another browser.",
        ) from exc


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
async def get_session(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    try:
        return _public(get_store().get(session_id), _holder(x_session_holder))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc


@router.post("/api/sessions/{session_id}/presence")
async def heartbeat_presence(session_id: str, body: PresenceRequest) -> dict:
    try:
        get_store().get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    try:
        return get_store().presence.heartbeat(session_id, body.holder_id)
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=423,
            detail="This session is open in another browser.",
        ) from exc


@router.post("/api/sessions/{session_id}/presence/release")
async def release_presence(session_id: str, body: PresenceRequest) -> dict:
    try:
        get_store().get(session_id)
    except KeyError:
        return {"holder_id": "", "is_holder": False, "interactive": False, "locked": False}
    return get_store().presence.release(session_id, body.holder_id)


@router.post("/api/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().chat(session_id, body.message, service_id=body.service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/approve")
async def approve(
    session_id: str,
    body: ApproveRequest | None = None,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    service_id = body.service_id if body else None
    try:
        session = get_store().approve(session_id, service_id=service_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().end_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/retry-ingest")
async def retry_ingest(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
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
    return _public(session, holder)


@router.get("/api/sessions/{session_id}/download/plan")
async def download_plan(session_id: str) -> PlainTextResponse:
    try:
        session = get_store().get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    text = plan_package_markdown(session)
    if not (session.package_markdown or session.plan_spec or session.services):
        raise HTTPException(status_code=400, detail="No plan spec has been approved yet.")
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plan-spec-{session_id}.md"'},
    )


@router.put("/api/sessions/{session_id}/git")
async def save_git(
    session_id: str,
    body: GitSaveRequest,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().save_git(
            session_id,
            git_repo_url=body.git_repo_url,
            ssh_private_key=body.ssh_private_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except GitAccessError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to save git access for session %s", session_id)
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/git/send")
async def send_git(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().send_git(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send git access for session %s", session_id)
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from exc
    return _public(session, holder)
