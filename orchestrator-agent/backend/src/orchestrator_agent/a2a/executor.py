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

from orchestrator_agent.config import get_settings
from orchestrator_agent.sessions import get_store


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
            message=new_text_message("Ingesting architect design package…"),
        )

        markdown = get_message_text(context.message) if context.message else ""
        if not markdown.strip():
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("Expected architect design-package markdown in the message."),
            )
            return

        try:
            session = get_store().ingest(markdown.strip())
        except ValueError as exc:
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(str(exc)),
            )
            return
        except Exception as exc:  # noqa: BLE001
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(f"Ingest failed: {exc}"),
            )
            return

        settings = get_settings()
        payload = {
            "design_session_id": session.design_session_id,
            "ui_url": f"{settings.public_base_url}/sessions/{session.design_session_id}",
            "topology": session.topology,
            "design_version": session.design_version,
            "phase": session.phase,
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
                f"Workflow {session.design_session_id} ready. Open UI: {payload['ui_url']}"
            ),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported for orchestrator workflows yet.")
