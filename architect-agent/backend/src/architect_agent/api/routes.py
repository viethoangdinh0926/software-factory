from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from architect_agent.config import get_settings
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


@router.post("/design", response_model=DesignStartResponse)
async def start_design_session(
    markdown: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> DesignStartResponse:
    """Single public entrypoint: accept a business-spec (or WIP design) markdown."""
    content = ""
    if file is not None:
        raw = await file.read()
        content = raw.decode("utf-8")
    elif markdown:
        content = markdown
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide form field `markdown` or upload a `.md` file as `file`.",
        )

    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Markdown body is empty.")

    try:
        session = get_store().start(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    settings = get_settings()
    last = session.messages[-1]["content"] if session.messages else None
    return DesignStartResponse(
        design_session_id=session.session_id,
        ui_url=f"{settings.public_base_url}/sessions/{session.session_id}",
        phase=session.phase,
        ready_for_design=session.ready_for_design,
        message=last,
    )


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    try:
        return get_store().get(session_id).to_public()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc


@router.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest) -> dict:
    try:
        session = get_store().chat(session_id, body.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return session.to_public()


@router.post("/api/sessions/{session_id}/approve")
async def approve(session_id: str) -> dict:
    try:
        session = get_store().approve(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown design session") from exc
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
