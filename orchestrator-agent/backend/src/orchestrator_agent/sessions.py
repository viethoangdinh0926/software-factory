from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

from orchestrator_agent.a2a.engineer import send_plan_spec, send_suspend
from orchestrator_agent.config import get_settings
from orchestrator_agent.graph import build_graph, initial_state
from orchestrator_agent.graph.nodes.common import decorate_service
from orchestrator_agent.package_parse import parse_design_package

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowSession:
    design_session_id: str
    created_at: str
    updated_at: str
    design_version: int = 0
    architect_track: str = "unset"
    topology: str = "unset"
    phase: str = "ingest"
    wait_kind: str = ""
    package_markdown: str = ""
    design_diagram: str = ""
    tech_stack: str = ""
    plan_spec: str = ""
    api_type: str = ""
    api_design: str = ""
    app_status: str = ""
    search_notes: str = ""
    services: list[dict[str, Any]] = field(default_factory=list)
    active_service_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    engineer_handoffs: list[dict[str, Any]] = field(default_factory=list)
    last_interrupt: dict[str, Any] | None = None
    last_handoff: dict[str, Any] | None = None
    can_approve: bool = False
    approve_kind: str = ""
    approve_label: str = ""
    finalized: bool = False

    @property
    def discussion_locked(self) -> bool:
        return (
            self.topology == "standalone"
            and self.app_status == "sent"
            and not self.finalized
        )

    def to_public(self) -> dict[str, Any]:
        interrupt = self.last_interrupt or {}
        locked = self.discussion_locked
        can_approve = bool(interrupt.get("can_approve", self.can_approve)) and not locked
        if self.finalized:
            can_approve = False
        last = self.engineer_handoffs[-1] if self.engineer_handoffs else self.last_handoff
        services = [decorate_service(s, finalized=self.finalized) for s in self.services]
        return {
            "design_session_id": self.design_session_id,
            "ui_path": f"/sessions/{self.design_session_id}",
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "design_version": self.design_version,
            "architect_track": self.architect_track,
            "topology": self.topology,
            "phase": self.phase,
            "wait_kind": interrupt.get("wait_kind") or self.wait_kind,
            "package_markdown": self.package_markdown,
            "design_diagram": self.design_diagram,
            "tech_stack": self.tech_stack,
            "plan_spec": self.plan_spec,
            "api_type": self.api_type,
            "api_design": self.api_design,
            "app_status": self.app_status,
            "search_notes": self.search_notes,
            "services": services,
            "active_service_id": interrupt.get("active_service_id") or self.active_service_id,
            "messages": self.messages,
            "engineer_handoffs": self.engineer_handoffs,
            "last_handoff": last,
            "can_approve": can_approve,
            "approve_kind": interrupt.get("approve_kind") or self.approve_kind,
            "approve_label": interrupt.get("approve_label") or self.approve_label,
            "discussion_locked": locked,
            "finalized": self.finalized,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowSession:
        return cls(
            design_session_id=str(data.get("design_session_id") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            design_version=int(data.get("design_version") or 0),
            architect_track=str(data.get("architect_track") or "unset"),
            topology=str(data.get("topology") or "unset"),
            phase=str(data.get("phase") or "ingest"),
            wait_kind=str(data.get("wait_kind") or ""),
            package_markdown=str(data.get("package_markdown") or ""),
            design_diagram=str(data.get("design_diagram") or ""),
            tech_stack=str(data.get("tech_stack") or ""),
            plan_spec=str(data.get("plan_spec") or ""),
            api_type=str(data.get("api_type") or ""),
            api_design=str(data.get("api_design") or ""),
            app_status=str(data.get("app_status") or ""),
            search_notes=str(data.get("search_notes") or ""),
            services=list(data.get("services") or []),
            active_service_id=str(data.get("active_service_id") or ""),
            messages=list(data.get("messages") or []),
            engineer_handoffs=list(data.get("engineer_handoffs") or []),
            last_interrupt=data.get("last_interrupt"),
            last_handoff=data.get("last_handoff"),
            can_approve=bool(data.get("can_approve")),
            approve_kind=str(data.get("approve_kind") or ""),
            approve_label=str(data.get("approve_label") or ""),
            finalized=bool(data.get("finalized")),
        )


class SessionStore:
    def __init__(self) -> None:
        self._graph = build_graph()
        self._cache: dict[str, WorkflowSession] = {}
        self._load_disk()

    def _session_path(self, session_id: str) -> Path:
        return get_settings().data_dir / f"{session_id}.json"

    def _inbound_dir(self) -> Path:
        path = get_settings().data_dir.parent / "handoffs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_disk(self) -> None:
        root = get_settings().data_dir
        if not root.is_dir():
            return
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session = WorkflowSession.from_dict(data)
                if session.design_session_id:
                    self._cache[session.design_session_id] = session
            except Exception:
                logger.exception("Failed to load workflow %s", path)

    def _persist(self, session: WorkflowSession) -> None:
        session.updated_at = _now()
        path = self._session_path(session.design_session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = session.to_public()
        payload["created_at"] = session.created_at
        payload["last_interrupt"] = session.last_interrupt
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._cache[session.design_session_id] = session

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = []
        for session in sorted(self._cache.values(), key=lambda s: s.updated_at, reverse=True):
            rows.append(
                {
                    "design_session_id": session.design_session_id,
                    "design_version": session.design_version,
                    "architect_track": session.architect_track,
                    "topology": session.topology,
                    "phase": session.phase,
                    "updated_at": session.updated_at,
                    "finalized": session.finalized,
                    "ui_path": f"/sessions/{session.design_session_id}",
                }
            )
        return rows

    def get(self, session_id: str) -> WorkflowSession:
        session = self._cache.get(session_id)
        if session is None:
            path = self._session_path(session_id)
            if path.is_file():
                session = WorkflowSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self._cache[session_id] = session
        if session is None:
            raise KeyError(session_id)
        return session

    def ingest(self, markdown: str) -> WorkflowSession:
        parsed = parse_design_package(markdown)
        if not parsed.design_session_id:
            raise ValueError("Design package is missing a design session ID.")
        session_id = parsed.design_session_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        inbound = self._inbound_dir() / f"{session_id}-v{parsed.design_version}-{stamp}.md"
        inbound.write_text(markdown, encoding="utf-8")

        existing = self._cache.get(session_id)
        if existing is None and self._session_path(session_id).is_file():
            existing = self.get(session_id)

        if existing is None:
            now = _now()
            session = WorkflowSession(
                design_session_id=session_id,
                created_at=now,
                updated_at=now,
                package_markdown=markdown,
            )
            self._cache[session_id] = session
            result = self._graph.invoke(
                initial_state(session_id, markdown),
                config=self._config(session_id),
            )
            self._apply_graph_result(session, result)
            self._flush_engineer_actions(session)
            self._persist(session)
            return session

        if existing.finalized:
            return existing

        result = self._graph.invoke(
            Command(resume={"action": "ingest", "text": markdown}),
            config=self._config(session_id),
        )
        self._apply_graph_result(existing, result, user_text=None, action="ingest")
        self._flush_engineer_actions(existing)
        self._persist(existing)
        return existing

    def resume(
        self, session_id: str, action: str, text: str = "", service_id: str | None = None
    ) -> WorkflowSession:
        session = self.get(session_id)
        if session.finalized:
            return session
        if (
            action in {"chat", "approve"}
            and session.discussion_locked
            and action != "ingest"
        ):
            raise PermissionError(
                "Stand-alone plan already handed off. Further discussion starts when "
                "the architect sends an updated design package."
            )
        result = self._graph.invoke(
            Command(resume={"action": action, "text": text, "service_id": service_id or ""}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result, user_text=text or None, action=action)
        self._flush_engineer_actions(session)
        self._persist(session)
        return session

    def chat(self, session_id: str, text: str, service_id: str | None = None) -> WorkflowSession:
        return self.resume(session_id, action="chat", text=text, service_id=service_id)

    def approve(self, session_id: str, service_id: str | None = None) -> WorkflowSession:
        return self.resume(session_id, action="approve", service_id=service_id)

    def end_session(self, session_id: str) -> WorkflowSession:
        return self.resume(session_id, action="session_done")

    def _flush_engineer_actions(self, session: WorkflowSession) -> None:
        state = self._graph.get_state(self._config(session.design_session_id))
        values = state.values or {}
        actions = list(values.get("pending_engineer_actions") or [])
        if not actions:
            return
        for item in actions:
            action = str(item.get("action") or "")
            if action == "plan":
                handoff = send_plan_spec(
                    design_session_id=str(item.get("design_session_id") or session.design_session_id),
                    design_version=int(item.get("design_version") or session.design_version or 1),
                    markdown=str(item.get("markdown") or ""),
                    microservice_id=item.get("microservice_id"),
                )
            elif action == "suspend":
                handoff = send_suspend(
                    design_session_id=str(item.get("design_session_id") or session.design_session_id),
                    design_version=int(item.get("design_version") or session.design_version or 1),
                    reason=str(item.get("reason") or "unspecified"),
                    microservice_id=item.get("microservice_id"),
                )
            else:
                continue
            public = handoff.to_public()
            session.engineer_handoffs.append(public)
            session.last_handoff = public
        try:
            self._graph.update_state(
                self._config(session.design_session_id),
                {"pending_engineer_actions": []},
            )
        except Exception:
            logger.exception("Failed to clear pending engineer actions for %s", session.design_session_id)

    def _apply_graph_result(
        self,
        session: WorkflowSession,
        result: dict[str, Any] | Any,
        *,
        user_text: str | None = None,
        action: str | None = None,
    ) -> None:
        state = self._graph.get_state(self._config(session.design_session_id))
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

        session.package_markdown = str(values.get("package_markdown") or session.package_markdown)
        session.design_diagram = str(values.get("design_diagram") or session.design_diagram)
        session.design_version = int(values.get("design_version") or session.design_version or 0)
        session.architect_track = str(values.get("architect_track") or session.architect_track)
        session.topology = str(values.get("topology") or session.topology)
        session.phase = str(values.get("phase") or session.phase)
        session.wait_kind = str(values.get("wait_kind") or session.wait_kind)
        session.tech_stack = str(values.get("tech_stack") or "")
        session.plan_spec = str(values.get("plan_spec") or "")
        session.api_type = str(values.get("api_type") or "")
        session.api_design = str(values.get("api_design") or "")
        session.app_status = str(values.get("app_status") or "")
        session.search_notes = str(values.get("search_notes") or "")
        session.services = list(values.get("services") or [])
        session.active_service_id = str(values.get("active_service_id") or "")
        session.messages = list(values.get("messages") or session.messages)
        session.finalized = bool(values.get("finalized", session.finalized))
        if isinstance(payload, dict):
            session.last_interrupt = payload
            session.phase = str(payload.get("phase") or session.phase)
            session.wait_kind = str(payload.get("wait_kind") or session.wait_kind)
            session.can_approve = bool(payload.get("can_approve"))
            session.approve_kind = str(payload.get("approve_kind") or "")
            session.approve_label = str(payload.get("approve_label") or "")
            if payload.get("active_service_id"):
                session.active_service_id = str(payload["active_service_id"])
        elif session.finalized:
            session.can_approve = False
            session.last_interrupt = None


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def reset_store() -> None:
    global _store
    _store = None
