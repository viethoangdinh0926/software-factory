from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

from architect_agent.a2a.system_manager import HandoffResult, send_design_package
from architect_agent.config import get_settings
from architect_agent.graph import build_graph, initial_state
from architect_agent.mermaid_sanitize import sanitize_mermaid

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DesignSession:
    session_id: str
    created_at: str
    updated_at: str
    phase: str = "spec_interview"
    business_spec: str = ""
    design_diagram: str = ""
    design_justification: str = ""
    ready_for_design: bool = False
    spec_approved: bool = False
    design_approved: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_interrupt: dict[str, Any] | None = None
    finalized: bool = False
    design_version: int = 0
    last_handoff: dict[str, Any] | None = None
    market_evaluation_report: str = ""
    market_evaluation_grade: str = ""
    market_evaluation_done: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "design_session_id": self.session_id,
            "phase": self.phase,
            "ready_for_design": self.ready_for_design,
            "spec_approved": self.spec_approved,
            "design_approved": self.design_approved,
            "finalized": self.finalized,
            "can_approve": bool(
                (self.phase == "spec_interview" and self.ready_for_design)
                or (self.phase == "market_research" and self.market_evaluation_done)
                or (self.phase == "system_design" and not self.finalized)
            ),
            "business_spec": self.business_spec,
            "design_diagram": self.design_diagram,
            "design_justification": self.design_justification,
            "market_evaluation_report": self.market_evaluation_report,
            "market_evaluation_grade": self.market_evaluation_grade,
            "market_evaluation_done": self.market_evaluation_done,
            "messages": self.messages,
            "ui_path": f"/sessions/{self.session_id}",
            "updated_at": self.updated_at,
            "design_version": self.design_version,
            "last_handoff": self.last_handoff,
        }


def _session_from_disk(data: dict[str, Any]) -> DesignSession:
    session_id = data.get("design_session_id") or data.get("session_id")
    if not session_id:
        raise KeyError("session file missing design_session_id")
    return DesignSession(
        session_id=session_id,
        created_at=str(data.get("created_at") or data.get("updated_at") or _now()),
        updated_at=str(data.get("updated_at") or _now()),
        phase=str(data.get("phase") or "spec_interview"),
        business_spec=str(data.get("business_spec") or ""),
        design_diagram=str(data.get("design_diagram") or ""),
        design_justification=str(data.get("design_justification") or ""),
        ready_for_design=bool(data.get("ready_for_design")),
        spec_approved=bool(data.get("spec_approved")),
        design_approved=bool(data.get("design_approved")),
        messages=list(data.get("messages") or []),
        last_interrupt=data.get("last_interrupt"),
        finalized=bool(data.get("finalized")),
        design_version=int(data.get("design_version") or 0),
        last_handoff=data.get("last_handoff"),
        market_evaluation_report=str(data.get("market_evaluation_report") or ""),
        market_evaluation_grade=str(data.get("market_evaluation_grade") or ""),
        market_evaluation_done=bool(data.get("market_evaluation_done")),
    )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, DesignSession] = {}
        self._graph = build_graph()
        self._settings = get_settings()
        self._settings.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._settings.data_dir / f"{session_id}.json"

    def _persist(self, session: DesignSession) -> None:
        session.updated_at = _now()
        self._path(session.session_id).write_text(
            json.dumps(session.to_public() | {"created_at": session.created_at}, indent=2),
            encoding="utf-8",
        )

    def get(self, session_id: str) -> DesignSession:
        if session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            path = self._path(session_id)
            if not path.is_file():
                raise KeyError(session_id)
            session = _session_from_disk(json.loads(path.read_text(encoding="utf-8")))
            self._sessions[session_id] = session
            logger.info("Restored design session %s from disk (phase=%s)", session_id, session.phase)

        # Ensure LangGraph is waiting on an interrupt so chat/approve can resume.
        self._ensure_graph_resumable(session)
        return session

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def _has_interrupt(self, session_id: str) -> bool:
        state = self._graph.get_state(self._config(session_id))
        return any(bool(task.interrupts) for task in state.tasks)

    def _wait_node(self, session: DesignSession) -> str:
        if session.phase == "market_research":
            return "market_wait"
        if session.phase == "system_design" or (
            session.spec_approved and session.market_evaluation_done
        ):
            return "design_wait"
        return "spec_wait"

    def _active_message_node(self, session: DesignSession) -> str:
        if session.phase == "system_design":
            return "system_design"
        if session.phase == "market_research":
            return "market_research"
        return "spec_interview"

    def _ensure_graph_resumable(self, session: DesignSession) -> None:
        """After process restart, rebuild a wait-node interrupt without rewriting chat history."""
        if session.finalized or session.phase == "done":
            return
        if self._has_interrupt(session.session_id):
            return

        config = self._config(session.session_id)
        wait_node = self._wait_node(session)
        last_assistant = ""
        for msg in reversed(session.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_assistant = str(msg["content"])
                break

        phase = (
            "system_design"
            if wait_node == "design_wait"
            else "market_research"
            if wait_node == "market_wait"
            else "spec_interview"
        )
        seed = {
            "session_id": session.session_id,
            "business_spec": session.business_spec,
            "messages": [],
            "phase": phase,
            "ready_for_design": session.ready_for_design,
            "spec_approved": session.spec_approved
            or wait_node in {"design_wait", "market_wait"},
            "design_diagram": session.design_diagram,
            "design_justification": session.design_justification,
            "design_approved": False,
            "pending_user_feedback": "",
            "publish_requested": False,
            "pending_assistant_message": (
                last_assistant
                or (
                    str(session.last_interrupt.get("assistant_message") or "")
                    if session.last_interrupt
                    else ""
                )
            ),
            "market_evaluation_report": session.market_evaluation_report,
            "market_evaluation_grade": session.market_evaluation_grade,
            "market_evaluation_done": session.market_evaluation_done
            or wait_node in {"market_wait", "design_wait"},
        }
        preserved_messages = list(session.messages)
        try:
            as_node = {
                "design_wait": "system_design",
                "market_wait": "market_research",
                "spec_wait": "spec_interview",
            }[wait_node]
            self._graph.update_state(config, seed, as_node=as_node)
            result = self._graph.invoke(None, config=config)
            self._apply_graph_result(
                session,
                result,
                preserve_messages=preserved_messages,
                append_assistant=False,
            )
            logger.info(
                "Rehydrated LangGraph interrupt for session %s at node %s",
                session.session_id,
                wait_node,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to rehydrate graph for session %s", session.session_id)
            session.messages = preserved_messages
            self._persist(session)

    def _apply_graph_result(
        self,
        session: DesignSession,
        result: dict[str, Any] | Any,
        *,
        user_text: str | None = None,
        action: str | None = None,
        preserve_messages: list[dict[str, Any]] | None = None,
        append_assistant: bool = True,
    ) -> None:
        """Sync artifacts from graph state; chat history is append-only on DesignSession."""
        state = self._graph.get_state(self._config(session.session_id))
        values = state.values or {}

        payload = None
        for task in state.tasks:
            if task.interrupts:
                payload = task.interrupts[0].value
                break
        if payload is None and isinstance(result, dict) and "__interrupt__" in result:
            inter = result["__interrupt__"]
            if inter:
                payload = inter[0].value if hasattr(inter[0], "value") else inter[0]

        if values.get("business_spec"):
            session.business_spec = values["business_spec"]
        if values.get("design_diagram"):
            session.design_diagram = sanitize_mermaid(values["design_diagram"])
        if values.get("design_justification"):
            session.design_justification = values["design_justification"]
        if values.get("market_evaluation_report"):
            session.market_evaluation_report = values["market_evaluation_report"]
        if values.get("market_evaluation_grade"):
            session.market_evaluation_grade = values["market_evaluation_grade"]
        if "market_evaluation_done" in values:
            session.market_evaluation_done = bool(values.get("market_evaluation_done"))
        session.ready_for_design = bool(values.get("ready_for_design", session.ready_for_design))
        session.spec_approved = bool(values.get("spec_approved", session.spec_approved))
        session.design_approved = bool(values.get("design_approved", session.design_approved))
        session.phase = values.get("phase", session.phase)

        if preserve_messages is not None:
            session.messages = list(preserve_messages)

        # Never replace UI history from the graph message channel — it is LLM context only.
        if user_text and action == "chat":
            node = self._active_message_node(session)
            last = session.messages[-1] if session.messages else None
            if not last or last.get("role") != "user" or last.get("content") != user_text:
                session.messages.append({"role": "user", "content": user_text, "node": node})

        if isinstance(payload, dict):
            session.last_interrupt = payload
            session.phase = payload.get("phase", session.phase)
            session.ready_for_design = bool(
                payload.get("ready_for_design", session.ready_for_design)
            )
            if payload.get("business_spec"):
                session.business_spec = payload["business_spec"]
            if payload.get("design_diagram") is not None:
                session.design_diagram = sanitize_mermaid(
                    payload.get("design_diagram") or session.design_diagram
                )
            if payload.get("design_justification") is not None:
                session.design_justification = (
                    payload.get("design_justification") or session.design_justification
                )
            if payload.get("market_evaluation_report"):
                session.market_evaluation_report = str(payload["market_evaluation_report"])
                session.market_evaluation_done = True
            if payload.get("market_evaluation_grade"):
                session.market_evaluation_grade = str(payload["market_evaluation_grade"])
            msg = payload.get("assistant_message")
            if (
                append_assistant
                and msg
                and (not session.messages or session.messages[-1].get("content") != msg)
            ):
                node = self._active_message_node(session)
                session.messages.append({"role": "assistant", "content": msg, "node": node})

        session.finalized = session.phase == "done"
        self._persist(session)

    def start(self, markdown: str) -> DesignSession:
        session_id = str(uuid.uuid4())
        session = DesignSession(
            session_id=session_id,
            created_at=_now(),
            updated_at=_now(),
            business_spec=markdown,
        )
        self._sessions[session_id] = session

        result = self._graph.invoke(
            initial_state(session_id, markdown),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result)
        return session

    def resume(self, session_id: str, action: str, text: str = "") -> DesignSession:
        session = self.get(session_id)
        if session.finalized:
            return session

        publishing_design = action == "approve" and session.phase == "system_design"
        # One resume runs: wait → (optional generate) → wait interrupt.
        result = self._graph.invoke(
            Command(resume={"action": action, "text": text}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result, user_text=text or None, action=action)

        if publishing_design or bool(
            (self._graph.get_state(self._config(session_id)).values or {}).get("publish_requested")
        ):
            # Do not update_state here — it would clear the wait-node interrupt.
            self._publish_design_to_system_manager(session)

        return session

    def _publish_design_to_system_manager(self, session: DesignSession) -> HandoffResult:
        session.design_version += 1
        markdown = self.final_design_markdown(session.session_id)
        handoff = send_design_package(
            session_id=session.session_id,
            markdown=markdown,
            version=session.design_version,
        )
        session.last_handoff = handoff.to_public()
        session.design_approved = True
        status_line = (
            f"Handoff v{session.design_version} → System Manager: {handoff.status}. "
            f"{handoff.detail}"
        )
        session.messages.append(
            {"role": "assistant", "content": status_line, "node": "system_design"}
        )
        self._persist(session)
        return handoff

    def chat(self, session_id: str, text: str) -> DesignSession:
        return self.resume(session_id, action="chat", text=text)

    def approve(self, session_id: str) -> DesignSession:
        return self.resume(session_id, action="approve")

    def current_spec_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        return session.business_spec

    def market_evaluation_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        report = (session.market_evaluation_report or "").strip()
        if not report:
            raise ValueError("Approve the business spec first to generate a market evaluation.")
        return report

    def final_design_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        version = max(session.design_version, 1)
        parts = [
            f"# System Design Package\n",
            f"Design session: `{session.session_id}`\n",
            f"Design version: `{version}`\n",
            f"Generated: {session.updated_at}\n",
            "\n---\n",
            "## Business Specification\n\n",
            session.business_spec.strip(),
            "\n\n---\n",
            "## Design Diagram\n\n",
            "```mermaid\n",
            (session.design_diagram or "").strip(),
            "\n```\n\n",
            "## Design Justification\n\n",
            (session.design_justification or "").strip(),
            "\n",
        ]
        return "".join(parts)


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
