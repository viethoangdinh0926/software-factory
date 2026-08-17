from __future__ import annotations

import json
import logging
import threading

from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from orchestrator_agent.config import get_settings
from orchestrator_agent.package_parse import parse_design_package
from orchestrator_agent.sessions import get_store
from orchestrator_agent.utils.auth import configure_ssl_verification, restore_ssl_verification

logger = logging.getLogger(__name__)


def _ingest_later(markdown: str) -> None:
    try:
        # Apply SSL verification settings for background thread
        original_ssl_verify = configure_ssl_verification()

        try:
            get_store().ingest(markdown)
        finally:
            # Restore original SSL verification setting
            restore_ssl_verification(original_ssl_verify)
    except Exception:
        logger.exception("Background ingest of architect package failed")


class OrchestratorAgentExecutor(AgentExecutor):
    """A2A executor: architect design-package markdown → workflow keyed by design_session_id."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Accepting architect design package…"),
        )

        markdown = get_message_text(context.message) if context.message else ""
        if not markdown.strip():
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("Expected architect design-package markdown in the message."),
            )
            return

        try:
            parsed = parse_design_package(markdown.strip())
            if not parsed.design_session_id:
                raise ValueError("Design package is missing a design session ID.")
        except ValueError as exc:
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(str(exc)),
            )
            return

        # Ack immediately. Full extract/prime can take minutes and used to time out A2A.
        thread = threading.Thread(
            target=_ingest_later,
            args=(markdown.strip(),),
            name=f"orchestrator-ingest-{parsed.design_session_id[:8]}",
            daemon=True,
        )
        thread.start()

        settings = get_settings()
        payload = {
            "design_session_id": parsed.design_session_id,
            "ui_url": f"{settings.public_base_url}/sessions/{parsed.design_session_id}",
            "topology": "unset",
            "design_version": parsed.design_version,
            "phase": "ingest",
            "assistant_message": (
                "Design package accepted. Orchestrator is opening planning tiles in the background."
            ),
        }
        await updater.add_artifact(
            parts=[
                new_text_part(
                    text=json.dumps(payload, indent=2),
                    media_type="application/json",
                )
            ]
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message(
                f"Workflow {parsed.design_session_id} accepted. Open UI: {payload['ui_url']}"
            ),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported for orchestrator workflows yet.")
