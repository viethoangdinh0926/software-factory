from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

from architect_agent.a2a.system_manager import HandoffResult, send_design_package
from architect_agent.config import get_settings
from architect_agent.graph import build_graph, initial_state


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
                or (self.phase == "system_design" and not self.finalized)
            ),
            "business_spec": self.business_spec,
            "design_diagram": self.design_diagram,
            "design_justification": self.design_justification,
            "messages": self.messages,
            "ui_path": f"/sessions/{self.session_id}",
            "updated_at": self.updated_at,
            "design_version": self.design_version,
            "last_handoff": self.last_handoff,
        }


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, DesignSession] = {}
        self._graph = build_graph()
        self._settings = get_settings()

    def _path(self, session_id: str) -> Path:
        return self._settings.data_dir / f"{session_id}.json"

    def _persist(self, session: DesignSession) -> None:
        session.updated_at = _now()
        self._path(session.session_id).write_text(
            json.dumps(session.to_public() | {"created_at": session.created_at}, indent=2),
            encoding="utf-8",
        )

    def get(self, session_id: str) -> DesignSession:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def _apply_graph_result(
        self,
        session: DesignSession,
        result: dict[str, Any] | Any,
        *,
        user_text: str | None = None,
        action: str | None = None,
    ) -> None:
        # When interrupted mid-node, checkpoint values may lag local node state.
        # Prefer interrupt payload for living design artifacts; never wipe chat history.
        state = self._graph.get_state(self._config(session.session_id))
        values = state.values or {}

        pending_interrupt = False
        payload = None
        for task in state.tasks:
            if task.interrupts:
                pending_interrupt = True
                payload = task.interrupts[0].value
                break
        if payload is None and isinstance(result, dict) and "__interrupt__" in result:
            pending_interrupt = True
            inter = result["__interrupt__"]
            if inter:
                payload = inter[0].value if hasattr(inter[0], "value") else inter[0]

        if values.get("business_spec"):
            session.business_spec = values["business_spec"]
        if values.get("design_diagram") and not pending_interrupt:
            session.design_diagram = values["design_diagram"]
        if values.get("design_justification") and not pending_interrupt:
            session.design_justification = values["design_justification"]
        session.ready_for_design = bool(values.get("ready_for_design", session.ready_for_design))
        session.spec_approved = bool(values.get("spec_approved", session.spec_approved))
        session.design_approved = bool(values.get("design_approved", session.design_approved))
        session.phase = values.get("phase", session.phase)

        # Only replace message history from checkpoint when the node has completed
        # (no open interrupt). Otherwise keep the live UI transcript and append.
        checkpoint_messages = list(values.get("messages") or [])
        if not pending_interrupt and checkpoint_messages:
            session.messages = checkpoint_messages

        if user_text and action == "chat":
            node = "system_design" if session.phase == "system_design" else "spec_interview"
            if session.phase == "spec_interview" or session.phase == "system_design":
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
            # Always refresh living design from the active design-node interrupt.
            if payload.get("design_diagram") is not None:
                session.design_diagram = payload.get("design_diagram") or session.design_diagram
            if payload.get("design_justification") is not None:
                session.design_justification = (
                    payload.get("design_justification") or session.design_justification
                )
            msg = payload.get("assistant_message")
            if msg and (not session.messages or session.messages[-1].get("content") != msg):
                node = "system_design" if session.phase == "system_design" else "spec_interview"
                session.messages.append({"role": "assistant", "content": msg, "node": node})

        session.finalized = session.design_approved and session.phase == "done"
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

    def _has_interrupt(self, session_id: str) -> bool:
        state = self._graph.get_state(self._config(session_id))
        return any(bool(task.interrupts) for task in state.tasks)

    def _continue_node(self, session: DesignSession) -> Any:
        """Re-enter the active node to produce the next assistant output + interrupt."""
        node = (
            "system_design"
            if session.phase in {"system_design", "done"} and session.spec_approved
            else "spec_interview"
        )
        if session.phase == "system_design" or (
            session.spec_approved and not session.design_approved
        ):
            node = "system_design"
        return self._graph.invoke(
            Command(goto=node),
            config=self._config(session.session_id),
        )

    def resume(self, session_id: str, action: str, text: str = "") -> DesignSession:
        session = self.get(session_id)
        if session.finalized:
            return session
        publishing_design = action == "approve" and session.phase == "system_design"
        result = self._graph.invoke(
            Command(resume={"action": action, "text": text}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result, user_text=text or None, action=action)

        if publishing_design:
            self._publish_design_to_system_manager(session)

        if session.finalized:
            return session
        # After a chat/publish resume the node returns to END; kick the next turn so the
        # UI receives an updated interview question or revised design immediately.
        if not self._has_interrupt(session_id):
            result = self._continue_node(session)
            self._apply_graph_result(session, result)
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
        # Mark that at least one approved package exists (downloadable), without closing the session.
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
