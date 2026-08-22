from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator_agent.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class EngineerHandoff:
    status: str  # sent | queued | failed
    handoff_id: str
    path: str
    target_url: str | None
    detail: str
    at: str
    action: str
    design_session_id: str
    design_version: int
    microservice_id: str | None

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plan_dir() -> Path:
    settings = get_settings()
    path = settings.data_dir.parent / "plan_specs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_async(coro_factory):
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

    thread = threading.Thread(target=worker, name="orchestrator-a2a-send", daemon=True)
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
    async with httpx.AsyncClient(timeout=60.0, verify=settings.ssl_verify) as httpx_client:
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


def _deliver(*, markdown: str, filename: str, action: str, design_session_id: str, design_version: int, microservice_id: str | None) -> EngineerHandoff:
    settings = get_settings()
    handoff_id = str(uuid.uuid4())
    path = _plan_dir() / filename
    header = (
        f"<!-- orchestrator-agent handoff id={handoff_id} action={action} "
        f"session={design_session_id} version={design_version} "
        f"microservice={microservice_id or '-'} at={_now()} -->\n\n"
    )
    path.write_text(header + markdown, encoding="utf-8")
    target = (settings.engineer_agent_url or "").strip() or None
    if not target:
        detail = (
            "ENGINEER_AGENT_URL is not set. Plan spec queued locally; "
            "will be deliverable once the Engineer agent is deployed."
        )
        logger.info("Queued engineer handoff %s at %s", handoff_id, path)
        return EngineerHandoff(
            status="queued",
            handoff_id=handoff_id,
            path=str(path),
            target_url=None,
            detail=detail,
            at=_now(),
            action=action,
            design_session_id=design_session_id,
            design_version=design_version,
            microservice_id=microservice_id,
        )
    try:
        response_detail = _run_async(lambda: _a2a_send(markdown, target))
        logger.info("Sent engineer handoff %s to %s", handoff_id, target)
        return EngineerHandoff(
            status="sent",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=response_detail[:2000],
            at=_now(),
            action=action,
            design_session_id=design_session_id,
            design_version=design_version,
            microservice_id=microservice_id,
        )
    except Exception as exc:
        detail = f"A2A delivery to Engineer failed: {exc}"
        logger.exception("Failed engineer handoff %s to %s", handoff_id, target)
        return EngineerHandoff(
            status="failed",
            handoff_id=handoff_id,
            path=str(path),
            target_url=target,
            detail=detail,
            at=_now(),
            action=action,
            design_session_id=design_session_id,
            design_version=design_version,
            microservice_id=microservice_id,
        )


def send_plan_spec(
    *,
    design_session_id: str,
    design_version: int,
    markdown: str,
    microservice_id: str | None = None,
) -> EngineerHandoff:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{microservice_id}" if microservice_id else ""
    filename = f"{design_session_id}-v{design_version}{suffix}-plan-{stamp}.md"
    return _deliver(
        markdown=markdown,
        filename=filename,
        action="plan",
        design_session_id=design_session_id,
        design_version=design_version,
        microservice_id=microservice_id,
    )


def send_suspend(
    *,
    design_session_id: str,
    design_version: int,
    reason: str,
    microservice_id: str | None = None,
) -> EngineerHandoff:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{microservice_id}" if microservice_id else ""
    filename = f"{design_session_id}-v{design_version}{suffix}-suspend-{stamp}.md"
    body = (
        f"# Suspend development\n\n"
        f"- action: `suspend`\n"
        f"- design_session_id: `{design_session_id}`\n"
        f"- design_version: `{design_version}`\n"
        f"- microservice_id: `{microservice_id or ''}`\n"
        f"- reason: `{reason}`\n"
    )
    return _deliver(
        markdown=body,
        filename=filename,
        action="suspend",
        design_session_id=design_session_id,
        design_version=design_version,
        microservice_id=microservice_id,
    )


def send_git_access(
    *,
    design_session_id: str,
    git_repo_url: str,
    ssh_private_key: str,
) -> EngineerHandoff:
    """POST git URL + SSH key to the engineer. Never writes the private key to plan_specs."""
    settings = get_settings()
    handoff_id = str(uuid.uuid4())
    target = (settings.engineer_agent_url or "").strip() or None
    if not target:
        return EngineerHandoff(
            status="failed",
            handoff_id=handoff_id,
            path="",
            target_url=None,
            detail="Engineer agent is not configured (ENGINEER_AGENT_URL). Save the key, then resend once the engineer is running.",
            at=_now(),
            action="git",
            design_session_id=design_session_id,
            design_version=0,
            microservice_id=None,
        )
    try:
        detail = _post_git(target, design_session_id, git_repo_url, ssh_private_key)
        logger.info("Sent git access for session %s to engineer", design_session_id)
        return EngineerHandoff(
            status="sent",
            handoff_id=handoff_id,
            path="",
            target_url=target,
            detail=detail[:2000],
            at=_now(),
            action="git",
            design_session_id=design_session_id,
            design_version=0,
            microservice_id=None,
        )
    except Exception:
        logger.exception("Failed to send git access for session %s", design_session_id)
        return EngineerHandoff(
            status="failed",
            handoff_id=handoff_id,
            path="",
            target_url=target,
            detail="Could not deliver git access to the engineer. You can resend.",
            at=_now(),
            action="git",
            design_session_id=design_session_id,
            design_version=0,
            microservice_id=None,
        )


def _post_git(target_url: str, design_session_id: str, git_repo_url: str, ssh_private_key: str) -> str:
    import httpx

    settings = get_settings()
    url = target_url.rstrip("/") + "/api/git"
    with httpx.Client(timeout=30.0, verify=settings.ssl_verify) as client:
        response = client.post(
            url,
            json={
                "design_session_id": design_session_id,
                "git_repo_url": git_repo_url,
                "ssh_private_key": ssh_private_key,
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            return "Engineer accepted git access."
        return str(payload.get("message") or "Engineer stored git access for this design session.")
