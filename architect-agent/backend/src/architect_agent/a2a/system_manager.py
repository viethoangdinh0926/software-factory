from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from architect_agent.config import get_settings

logger = logging.getLogger(__name__)


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


async def _a2a_send(markdown: str, target_url: str) -> str:
    from a2a.client import ClientConfig, create_client
    from a2a.client.card_resolver import A2ACardResolver
    from a2a.helpers import new_text_message
    from a2a.types import Role, SendMessageRequest
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as httpx_client:
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
            return " | ".join(chunks) if chunks else "A2A send completed (empty response stream)."
        finally:
            await client.close()


def send_design_package(
    *,
    session_id: str,
    markdown: str,
    version: int,
) -> HandoffResult:
    """Persist the approved design package and deliver it to the System Manager agent via A2A.

    When SYSTEM_MANAGER_AGENT_URL is unset or the peer is unreachable, the package is still
    written under data/handoffs/ with status ``queued`` / ``failed`` so delivery can be retried
    once the System Manager agent exists.
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

    target = (settings.system_manager_agent_url or "").strip() or None
    if not target:
        detail = (
            "SYSTEM_MANAGER_AGENT_URL is not set. Design package queued locally; "
            "will be deliverable once the System Manager agent is deployed."
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
        response_detail = asyncio.run(_a2a_send(markdown, target))
        logger.info("Sent design handoff %s to %s", handoff_id, target)
        return HandoffResult(
            status="sent",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=response_detail[:2000],
            at=_now(),
        )
    except Exception as exc:  # noqa: BLE001
        detail = f"A2A delivery to System Manager failed: {exc}"
        logger.exception("Failed design handoff %s to %s", handoff_id, target)
        return HandoffResult(
            status="failed",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=detail,
            at=_now(),
        )
