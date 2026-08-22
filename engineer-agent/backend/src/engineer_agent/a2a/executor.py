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

from engineer_agent.config import get_settings
from engineer_agent.plan_parse import parse_handoff
from engineer_agent.sessions import get_store
from engineer_agent.utils.auth import configure_ssl_verification, restore_ssl_verification

logger = logging.getLogger(__name__)


def _ingest_later(markdown: str) -> None:
    try:
        original_ssl_verify = configure_ssl_verification()
        try:
            get_store().ingest(markdown)
        finally:
            restore_ssl_verification(original_ssl_verify)
    except Exception:
        logger.exception("Background ingest of plan spec failed")


class EngineerAgentExecutor(AgentExecutor):
    """A2A executor: orchestrator plan/suspend markdown → sub-engineer in a fleet."""

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
            message=new_text_message("Accepting orchestrator plan spec…"),
        )

        markdown = get_message_text(context.message) if context.message else ""
        if not markdown.strip():
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("Expected plan-spec or suspend markdown in the message."),
            )
            return

        try:
            parsed = parse_handoff(markdown.strip())
        except ValueError as exc:
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(str(exc)),
            )
            return

        thread = threading.Thread(
            target=_ingest_later,
            args=(markdown.strip(),),
            name=f"engineer-ingest-{parsed.design_session_id[:8]}",
            daemon=True,
        )
        thread.start()

        settings = get_settings()
        payload = {
            "design_session_id": parsed.design_session_id,
            "microservice_id": parsed.microservice_id,
            "ui_url": f"{settings.public_base_url}/sessions/{parsed.design_session_id}",
            "action": parsed.action,
            "assistant_message": (
                "Plan spec accepted. Engineer is opening the sub-agent in the background."
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
                f"Fleet {parsed.design_session_id} accepted. Open UI: {payload['ui_url']}"
            ),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported for engineer fleets yet.")
