from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

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
                or (self.phase == "system_design" and not self.design_approved)
            ),
            "business_spec": self.business_spec,
            "design_diagram": self.design_diagram,
            "design_justification": self.design_justification,
            "messages": self.messages,
            "ui_path": f"/sessions/{self.session_id}",
            "updated_at": self.updated_at,
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

    def _apply_graph_result(self, session: DesignSession, result: dict[str, Any] | Any) -> None:
        # When interrupted, result may be empty / __interrupt__
        state = self._graph.get_state(self._config(session.session_id))
        values = state.values or {}
        session.business_spec = values.get("business_spec", session.business_spec)
        session.design_diagram = values.get("design_diagram", session.design_diagram)
        session.design_justification = values.get(
            "design_justification", session.design_justification
        )
        session.ready_for_design = bool(values.get("ready_for_design", session.ready_for_design))
        session.spec_approved = bool(values.get("spec_approved", session.spec_approved))
        session.design_approved = bool(values.get("design_approved", session.design_approved))
        session.phase = values.get("phase", session.phase)
        session.messages = list(values.get("messages") or session.messages)
        session.finalized = session.design_approved and session.phase == "done"

        interrupts = state.tasks
        payload = None
        for task in interrupts:
            if task.interrupts:
                payload = task.interrupts[0].value
                break
        if payload is None and isinstance(result, dict) and "__interrupt__" in result:
            inter = result["__interrupt__"]
            if inter:
                payload = inter[0].value if hasattr(inter[0], "value") else inter[0]

        if isinstance(payload, dict):
            session.last_interrupt = payload
            session.phase = payload.get("phase", session.phase)
            session.ready_for_design = bool(
                payload.get("ready_for_design", session.ready_for_design)
            )
            if payload.get("business_spec"):
                session.business_spec = payload["business_spec"]
            if payload.get("design_diagram"):
                session.design_diagram = payload["design_diagram"]
            if payload.get("design_justification"):
                session.design_justification = payload["design_justification"]
            msg = payload.get("assistant_message")
            if msg and (not session.messages or session.messages[-1].get("content") != msg):
                node = "system_design" if session.phase == "system_design" else "spec_interview"
                session.messages.append({"role": "assistant", "content": msg, "node": node})

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
        result = self._graph.invoke(
            Command(resume={"action": action, "text": text}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result)
        return session

    def chat(self, session_id: str, text: str) -> DesignSession:
        return self.resume(session_id, action="chat", text=text)

    def approve(self, session_id: str) -> DesignSession:
        return self.resume(session_id, action="approve")

    def current_spec_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        return session.business_spec

    def final_design_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        parts = [
            f"# System Design Package\n",
            f"Design session: `{session.session_id}`\n",
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
