from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from architect_agent.config import get_settings
from architect_agent.session_presence import SessionBusyError
from architect_agent.sessions import get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["design"])


class DesignStartResponse(BaseModel):
    design_session_id: str
    ui_url: str
    phase: str
    ready_for_design: bool
    message: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class PresenceRequest(BaseModel):
    holder_id: str = Field(min_length=1)


def _holder(x_session_holder: str | None) -> str | None:
    value = (x_session_holder or "").strip()
    return value or None


def _public(session, holder: str | None = None) -> dict:
    data = session.to_public()
    data["interaction"] = get_store().presence.snapshot(session.session_id, holder)
    return data


def _require_interaction(session_id: str, holder: str | None) -> None:
    try:
        get_store().presence.require(session_id, holder)
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=423,
            detail="This session is open in another browser.",
        ) from exc


@router.post("/design", response_model=DesignStartResponse)
async def start_design_session(
    markdown: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> DesignStartResponse:
    """Single public entrypoint: accept a business-spec (or WIP design) markdown."""
    logger.info("Starting design session")
    content = ""
    if file is not None:
        logger.info("Processing uploaded file")
        raw = await file.read()
        content = raw.decode("utf-8")
    elif markdown:
        logger.info("Processing markdown form field")
        content = markdown
    else:
        logger.error("No markdown or file provided")
        raise HTTPException(
            status_code=400,
            detail="Provide form field `markdown` or upload a `.md` file as `file`.",
        )

    content = content.strip()
    if not content:
        logger.error("Empty markdown content")
        raise HTTPException(status_code=400, detail="Markdown body is empty.")

    logger.info("Content length: %d characters", len(content))
    
    try:
        logger.info("Calling get_store().start()")
        session = get_store().start(content)
        logger.info("Session created successfully: %s", session.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to start design session")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    settings = get_settings()
    last = session.messages[-1]["content"] if session.messages else None
    logger.info("Returning design response for session %s", session.session_id)
    return DesignStartResponse(
        design_session_id=session.session_id,
        ui_url=f"{settings.public_base_url}/sessions/{session.session_id}",
        phase=session.phase,
        ready_for_design=session.ready_for_design,
        message=last,
    )


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
        session = get_store().chat(session_id, body.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/approve")
async def approve(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().approve(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _public(session, holder)


@router.post("/api/sessions/{session_id}/retry-handoff")
async def retry_handoff(
    session_id: str,
    x_session_holder: str | None = Header(default=None),
) -> dict:
    holder = _holder(x_session_holder)
    _require_interaction(session_id, holder)
    try:
        session = get_store().retry_orchestrator_handoff(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Retry handoff failed for session %s", session_id)
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


@router.get("/api/sessions/{session_id}/download/spec")
async def download_spec(session_id: str) -> PlainTextResponse:
    try:
        text = get_store().current_spec_markdown(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="business-spec-{session_id}.md"'
        },
    )


@router.get("/api/sessions/{session_id}/download/market-evaluation")
async def download_market_evaluation(session_id: str) -> PlainTextResponse:
    try:
        text = get_store().market_evaluation_markdown(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="market-evaluation-{session_id}.md"'
            )
        },
    )


@router.get("/api/sessions/{session_id}/download/final")
async def download_final(session_id: str) -> PlainTextResponse:
    try:
        session = get_store().get(session_id)
        if not session.design_approved and session.design_version < 1:
            raise HTTPException(
                status_code=400,
                detail="Approve a design version before downloading the design package.",
            )
        text = get_store().final_design_markdown(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="design-package-{session_id}.md"'
        },
    )
