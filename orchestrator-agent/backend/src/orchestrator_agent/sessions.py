from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

from orchestrator_agent.a2a.engineer import send_git_access, send_plan_spec, send_suspend
from orchestrator_agent.ascii_text import fold_to_ascii
from orchestrator_agent.config import get_settings
from orchestrator_agent.git_access import (
    GitAccessError,
    key_fingerprint,
    validate_private_key,
    validate_repo_url,
    verify_repo_access,
)
from orchestrator_agent.graph import build_graph, initial_state
from orchestrator_agent.graph.nodes.common import decorate_service
from orchestrator_agent.package_parse import ParsedPackage, parse_design_package
from orchestrator_agent.query_intent import (
    classify_user_message,
    format_classify_context,
    workflow_action,
)
from orchestrator_agent.workflow import session_workflow_tiles, set_workflow_position
from orchestrator_agent.secrets_store import load_git_secrets, save_git_secrets

logger = logging.getLogger(__name__)

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
    entity_relationships: str = ""
    feature_spec: str = ""
    app_status: str = ""
    search_notes: str = ""
    discussion_digest: str = ""
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
    git_repo_url: str = ""
    git_ssh_private_key: str = ""
    git_key_fingerprint: str = ""
    git_send_status: str = ""
    git_send_error: str = ""
    git_sent_at: str = ""
    git_sent_fingerprint: str = ""

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
            "entity_relationships": self.entity_relationships,
            "feature_spec": self.feature_spec,
            "app_status": self.app_status,
            "search_notes": self.search_notes,
            "discussion_digest": self.discussion_digest,
            "services": services,
            "active_service_id": interrupt.get("active_service_id") or self.active_service_id,
            "messages": [
                {**m, "content": fold_to_ascii(str(m.get("content") or ""))} if isinstance(m, dict) else m
                for m in self.messages
            ],
            "workflow": session_workflow_tiles(self),
            "engineer_handoffs": self.engineer_handoffs,
            "last_handoff": last,
            "can_approve": can_approve,
            "approve_kind": interrupt.get("approve_kind") or self.approve_kind,
            "approve_label": interrupt.get("approve_label") or self.approve_label,
            "discussion_locked": locked,
            "finalized": self.finalized,
            "git_repo_url": self.git_repo_url,
            "git_key_configured": bool(self.git_ssh_private_key),
            "git_key_fingerprint": self.git_key_fingerprint,
            "git_send_status": self.git_send_status,
            "git_send_error": self.git_send_error,
            "git_sent_at": self.git_sent_at,
            "git_sent_fingerprint": self.git_sent_fingerprint,
            "can_send_git": bool(self.git_repo_url and self.git_ssh_private_key) and not self.finalized,
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
            entity_relationships=str(data.get("entity_relationships") or ""),
            feature_spec=str(data.get("feature_spec") or ""),
            app_status=str(data.get("app_status") or ""),
            search_notes=str(data.get("search_notes") or ""),
            discussion_digest=str(data.get("discussion_digest") or ""),
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
            git_repo_url=str(data.get("git_repo_url") or ""),
            git_key_fingerprint=str(data.get("git_key_fingerprint") or ""),
            git_send_status=str(data.get("git_send_status") or ""),
            git_send_error=str(data.get("git_send_error") or ""),
            git_sent_at=str(data.get("git_sent_at") or ""),
            git_sent_fingerprint=str(data.get("git_sent_fingerprint") or ""),
        )


class SessionStore:
    def __init__(self) -> None:
        self._graph = build_graph()
        self._cache: dict[str, WorkflowSession] = {}
        self._ingest_locks: dict[str, threading.Lock] = {}
        self._ingest_locks_guard = threading.Lock()
        self._load_disk()

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._ingest_locks_guard:
            lock = self._ingest_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._ingest_locks[session_id] = lock
            return lock

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
                    self._hydrate_git_secrets(session)
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
        if session.git_repo_url or session.git_ssh_private_key:
            save_git_secrets(
                session.design_session_id,
                git_repo_url=session.git_repo_url,
                ssh_private_key=session.git_ssh_private_key,
            )
        self._cache[session.design_session_id] = session

    def _hydrate_git_secrets(self, session: WorkflowSession) -> None:
        secrets = load_git_secrets(session.design_session_id)
        if secrets.get("git_repo_url") and not session.git_repo_url:
            session.git_repo_url = secrets["git_repo_url"]
        if secrets.get("ssh_private_key"):
            session.git_ssh_private_key = secrets["ssh_private_key"]
            session.git_key_fingerprint = key_fingerprint(session.git_ssh_private_key)

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
                self._hydrate_git_secrets(session)
                self._cache[session_id] = session
        if session is None:
            raise KeyError(session_id)
        return session

    def ingest(self, markdown: str) -> WorkflowSession:
        parsed = parse_design_package(markdown)
        if not parsed.design_session_id:
            raise ValueError("Design package is missing a design session ID.")
        with self._lock_for(parsed.design_session_id):
            return self._ingest_locked(parsed, markdown)

    def _ingest_locked(self, parsed: ParsedPackage, markdown: str) -> WorkflowSession:
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
            
            # Persist the session immediately to prevent data loss
            self._persist(session)
            
            try:
                set_workflow_position(str(session_workflow_tiles(session).get("title") or "Architect package"))
                result = self._graph.invoke(
                    initial_state(session_id, markdown),
                    config=self._config(session_id),
                )
                self._apply_graph_result(session, result)
                self._flush_engineer_actions(session)
                self._persist(session)
            except Exception as exc:
                logger.exception("Graph invocation failed during ingest for session %s", session_id)
                # Session is already persisted with basic info, so it's not lost
                # Add error message to session messages
                session.messages.append({
                    "role": "system",
                    "content": f"Ingest encountered an error: {str(exc)}. The package has been saved and can be retried.",
                    "node": "ingest"
                })
                self._persist(session)
                raise
            
            return session

        if existing.finalized:
            return existing

        # If the existing session has ingest errors, clear them and notify about new package
        has_ingest_errors = any(
            msg.get("role") == "system" and "encountered an error" in msg.get("content", "")
            for msg in existing.messages
        )
        
        if has_ingest_errors:
            # Clear previous error messages
            existing.messages = [
                msg for msg in existing.messages
                if not (msg.get("role") == "system" and "encountered an error" in msg.get("content", ""))
            ]
            # Add notification about new package
            existing.messages.append({
                "role": "system",
                "content": "A new design package was received. Previous failed ingest has been replaced.",
                "node": "ingest"
            })
            # Update the package markdown to the new version
            existing.package_markdown = markdown
            self._persist(existing)

        try:
            set_workflow_position(str(session_workflow_tiles(existing).get("title") or "Architect package"))
            result = self._graph.invoke(
                Command(resume={"action": "ingest", "text": markdown}),
                config=self._config(session_id),
            )
            self._apply_graph_result(existing, result, user_text=None, action="ingest")
            self._flush_engineer_actions(existing)
            self._persist(existing)
        except Exception as exc:
            logger.exception("Graph invocation failed during resume for session %s", session_id)
            # Add error message to session messages
            existing.messages.append({
                "role": "system",
                "content": f"Resume encountered an error: {str(exc)}. The package has been saved and can be retried.",
                "node": "ingest"
            })
            self._persist(existing)
            raise
            
        return existing

    def resume(
        self, session_id: str, action: str, text: str = "", service_id: str | None = None
    ) -> WorkflowSession:
        session = self.get(session_id)
        if session.finalized:
            return session
        if (
            action in {"chat", "approve", "revise", "answer"}
            and session.discussion_locked
            and action != "ingest"
        ):
            raise PermissionError(
                "Stand-alone plan already handed off. Further discussion starts when "
                "the architect sends an updated design package."
            )
        title = str(session_workflow_tiles(session).get("title") or session.phase or "plan")
        if service_id:
            svc = next(
                (
                    item
                    for item in session.services
                    if str(item.get("microservice_id") or "") == service_id
                ),
                None,
            )
            if svc:
                title = str(decorate_service(svc).get("workflow", {}).get("title") or title)
        set_workflow_position(title)
        result = self._graph.invoke(
            Command(resume={"action": action, "text": text, "service_id": service_id or ""}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result, user_text=text or None, action=action)
        self._flush_engineer_actions(session)
        self._persist(session)
        return session

    def chat(self, session_id: str, text: str, service_id: str | None = None) -> WorkflowSession:
        session = self.get(session_id)
        can_approve = False
        if service_id:
            svc = next(
                (
                    item
                    for item in session.services
                    if str(item.get("microservice_id") or "") == service_id
                ),
                None,
            )
            can_approve = bool(
                svc and decorate_service(svc, finalized=session.finalized).get("can_approve")
            )
        else:
            can_approve = bool(session.to_public().get("can_approve"))
        last = ""
        for msg in reversed(session.messages or []):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last = str(msg.get("content") or "")
                break
        context = format_classify_context(
            workflow=(
                f"Orchestrator current step: {session_workflow_tiles(session).get('title')}. "
                f"phase={session.phase} wait={session.wait_kind} "
                f"service={service_id or 'session'} can_approve={can_approve}. "
                "Stay on this step unless they approved advancing."
            ),
            last_assistant=last,
        )
        _category, action = classify_user_message(text, context)
        return self.resume(
            session_id, action=workflow_action(action), text=text, service_id=service_id
        )

    def approve(self, session_id: str, service_id: str | None = None) -> WorkflowSession:
        return self.resume(session_id, action="approve", service_id=service_id)

    def end_session(self, session_id: str) -> WorkflowSession:
        return self.resume(session_id, action="session_done")

    def save_git(
        self,
        session_id: str,
        *,
        git_repo_url: str,
        ssh_private_key: str | None = None,
    ) -> WorkflowSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            url = validate_repo_url(git_repo_url)
            incoming = (ssh_private_key or "").strip()
            if incoming:
                key = validate_private_key(ssh_private_key or "")
            elif session.git_ssh_private_key:
                key = session.git_ssh_private_key
            else:
                raise GitAccessError("Paste an SSH private key for this repo.")
            fingerprint = key_fingerprint(key)
            changed = url != session.git_repo_url or fingerprint != session.git_key_fingerprint
            session.git_repo_url = url
            session.git_ssh_private_key = key
            session.git_key_fingerprint = fingerprint
            if changed:
                session.git_send_status = ""
                session.git_send_error = ""
            self._persist(session)
            return session

    def send_git(self, session_id: str) -> WorkflowSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            session.git_send_error = ""
            try:
                if not session.git_repo_url or not session.git_ssh_private_key:
                    raise GitAccessError("Save a git repo URL and SSH private key before sending.")
                verify_repo_access(session.git_repo_url, session.git_ssh_private_key)
                delivery = send_git_access(
                    design_session_id=session.design_session_id,
                    git_repo_url=session.git_repo_url,
                    ssh_private_key=session.git_ssh_private_key,
                )
            except GitAccessError as exc:
                session.git_send_status = "failed"
                session.git_send_error = exc.user_message
                self._persist(session)
                return session
            if delivery.status == "sent":
                session.git_send_status = "sent"
                session.git_send_error = ""
                session.git_sent_at = delivery.at
                session.git_sent_fingerprint = session.git_key_fingerprint
            else:
                session.git_send_status = "failed"
                session.git_send_error = (
                    delivery.detail
                    or "Could not deliver git access to the engineer. You can resend."
                )
            public = delivery.to_public()
            session.engineer_handoffs.append(public)
            session.last_handoff = public
            self._persist(session)
            return session

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
        session.feature_spec = str(values.get("feature_spec") or session.feature_spec or "")
        session.plan_spec = str(values.get("plan_spec") or "")
        session.api_type = str(values.get("api_type") or "")
        session.api_design = str(values.get("api_design") or "")
        session.entity_relationships = str(values.get("entity_relationships") or "")
        session.app_status = str(values.get("app_status") or "")
        session.search_notes = str(values.get("search_notes") or "")
        session.discussion_digest = str(values.get("discussion_digest") or session.discussion_digest)
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
