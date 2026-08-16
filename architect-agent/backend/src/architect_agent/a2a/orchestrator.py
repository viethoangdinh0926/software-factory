from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from architect_agent.config import get_settings

logger = logging.getLogger(__name__)

_UI_URL_RE = re.compile(r"https?://[^\s\"']+/sessions/[0-9a-fA-F-]{36}")


@dataclass
class HandoffResult:
    status: str  # sent | queued | failed
    handoff_id: str
    path: str
    target_url: str | None
    detail: str
    at: str

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handoff_dir() -> Path:
    settings = get_settings()
    path = settings.data_dir.parent / "handoffs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_ui_url(detail: str) -> str | None:
    """Pull orchestrator UI URL from A2A response chunks when present."""
    if not detail:
        return None
    match = _UI_URL_RE.search(detail)
    if match:
        return match.group(0)
    try:
        start = detail.find("{")
        if start < 0:
            return None
        parsed = json.JSONDecoder().raw_decode(detail[start:])[0]
        if isinstance(parsed, dict):
            url = parsed.get("ui_url")
            if isinstance(url, str) and url.strip():
                return url.strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return None


def _run_async(coro_factory):
    """Run an async A2A send from sync LangGraph/FastAPI code.

    ``asyncio.run()`` raises if FastAPI already has an event loop; hop to a
    worker thread in that case so the package still goes out.
    """

    def _call():
        return asyncio.run(coro_factory())

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _call()
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["ok"] = _call()
        except BaseException as exc:  # noqa: BLE001
            box["err"] = exc

    thread = threading.Thread(target=worker, name="architect-a2a-send", daemon=True)
    thread.start()
    thread.join()
    if "err" in box:
        raise box["err"]
    return box["ok"]


async def _a2a_send(markdown: str, target_url: str) -> str:
    import httpx

    from a2a.client import ClientConfig, create_client
    from a2a.client.card_resolver import A2ACardResolver
    from a2a.helpers import new_text_message
    from a2a.types import Role, SendMessageRequest

    settings = get_settings()
    async with httpx.AsyncClient(timeout=180.0, verify=settings.ssl_verify) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=target_url.rstrip("/"))
        card = await resolver.get_agent_card()
        client = await create_client(
            agent=card,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )
        try:
            message = new_text_message(markdown, role=Role.ROLE_USER, media_type="text/markdown")
            request = SendMessageRequest(message=message)
            chunks: list[str] = []
            async for chunk in client.send_message(request):
                chunks.append(str(chunk))
            joined = " | ".join(chunks) if chunks else "A2A send completed (empty response stream)."
            ui_url = _extract_ui_url(joined)
            if ui_url:
                return f"Orchestrator UI: {ui_url}. {joined}"
            return joined
        finally:
            await client.close()


def strip_handoff_header(markdown: str) -> str:
    """Drop the architect handoff HTML comment so a retry does not stack headers."""
    text = markdown or ""
    stripped = text.lstrip()
    if stripped.startswith("<!--") and "architect-agent handoff" in stripped[:200]:
        end = stripped.find("-->")
        if end >= 0:
            return stripped[end + 3 :].lstrip()
    return text


def retry_design_package(
    *,
    session_id: str,
    version: int,
    saved_path: str | None = None,
    markdown: str | None = None,
) -> HandoffResult:
    """Resend an already-versioned package. Does not allocate a new design version."""
    body = markdown or ""
    if saved_path:
        path = Path(saved_path)
        if path.is_file():
            body = strip_handoff_header(path.read_text(encoding="utf-8"))
    if not body.strip():
        raise ValueError("No saved design package to retry.")
    return send_design_package(session_id=session_id, markdown=body, version=version)


def send_design_package(
    *,
    session_id: str,
    markdown: str,
    version: int,
) -> HandoffResult:
    """Persist the approved design package and deliver it to the Orchestrator agent via A2A.

    When ORCHESTRATOR_AGENT_URL is unset or the peer is unreachable, the package is still
    written under data/handoffs/ with status ``queued`` / ``failed`` so delivery can be retried
    once the Orchestrator agent exists.
    """
    settings = get_settings()
    handoff_id = str(uuid.uuid4())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{session_id}-v{version}-{stamp}.md"
    path = _handoff_dir() / filename
    header = (
        f"<!-- architect-agent handoff id={handoff_id} session={session_id} "
        f"version={version} at={_now()} -->\n\n"
    )
    path.write_text(header + markdown, encoding="utf-8")

    target = (settings.orchestrator_agent_url or "").strip() or None
    if not target:
        detail = (
            "ORCHESTRATOR_AGENT_URL is not set. Design package queued locally; "
            "will be deliverable once the Orchestrator agent is deployed."
        )
        logger.info("Queued design handoff %s at %s", handoff_id, path)
        return HandoffResult(
            status="queued",
            handoff_id=handoff_id,
            path=str(path),
            target_url=None,
            detail=detail,
            at=_now(),
        )

    try:
        response_detail = _run_async(lambda: _a2a_send(markdown, target))
        logger.info("Sent design handoff %s to %s", handoff_id, target)
        return HandoffResult(
            status="sent",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=response_detail[:2000],
            at=_now(),
        )
    except Exception as exc:
        detail = f"A2A delivery to Orchestrator failed: {exc}"
        logger.exception("Failed design handoff %s to %s", handoff_id, target)
        return HandoffResult(
            status="failed",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=detail,
            at=_now(),
        )
