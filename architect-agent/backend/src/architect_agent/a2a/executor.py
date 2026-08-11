from __future__ import annotations

import json

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

from architect_agent.config import get_settings
from architect_agent.sessions import get_store


class ArchitectAgentExecutor(AgentExecutor):
    """A2A executor: markdown in message → start design session, return session id + UI URL."""

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
            message=new_text_message("Starting architect design session…"),
        )

        markdown = get_message_text(context.message) if context.message else ""
        if not markdown.strip():
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("Expected markdown business specification in the message."),
            )
            return

        session = get_store().start(markdown.strip())
        settings = get_settings()
        payload = {
            "design_session_id": session.session_id,
            "ui_url": f"{settings.public_base_url}/sessions/{session.session_id}",
            "phase": session.phase,
            "ready_for_design": session.ready_for_design,
            "assistant_message": session.messages[-1]["content"] if session.messages else None,
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
                f"Design session {session.session_id} created. Open UI: {payload['ui_url']}"
            ),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported for architect design sessions yet.")
