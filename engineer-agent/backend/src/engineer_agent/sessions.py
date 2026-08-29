from __future__ import annotations

import json
import logging
import queue
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineer_agent.ascii_text import fold_to_ascii
from engineer_agent.config import get_settings
from engineer_agent.discussion_memory import (
    DISCUSSION_MEMORY_RULES,
    consult_user_turn,
    format_phase_context,
    refresh_discussion_digest,
)
from engineer_agent.execution import (
    all_items_terminal,
    apply_transition,
    clear_blocked_items,
    has_plan_items,
    next_runnable_item,
    normalize_plan,
    replace_item,
    snapshot_peer_contracts,
)
from engineer_agent.llm import invoke_json
from engineer_agent.plan_parse import ParsedHandoff, parse_handoff, parse_related_entities, sub_agent_id
from engineer_agent.session_presence import SessionPresence
from engineer_agent.query_intent import (
    SUGGESTED_ANSWER_RULES,
    user_message_first_block,
    classify_user_message,
    format_classify_context,
    with_next_prompt,
    with_resolution_close,
)
from engineer_agent.secrets_store import load_git_secrets, save_git_secrets
from engineer_agent.workspace import (
    prepare_workspace,
    private_dir_name,
    run_workspace_tests,
    ship_workspace,
    write_item_work,
)

logger = logging.getLogger(__name__)

APPROVE_LABELS = {
    "awaiting_plan": "Approve plan",
    "blocked": "Approve to continue",
}

PLAN_EDITABLE = {"awaiting_plan", "paused"}

_PEER_NEED_RE = (
    "need more",
    "need less",
    "more data",
    "less data",
    "add field",
    "remove field",
    "drop field",
    "include ",
    "provide ",
    "from ",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skill_digest() -> str:
    path = get_settings().skill_path
    try:
        text = path.read_text(encoding="utf-8")[:3500]
    except OSError:
        text = "You are a Software Factory Engineer sub-agent for one microservice."
    return f"{text}\n\n{DISCUSSION_MEMORY_RULES}"


def _discussion_block(sub: dict[str, Any]) -> str:
    return format_phase_context(
        str(sub.get("discussion_digest") or ""),
        list(sub.get("messages") or []),
        str(sub.get("status") or "sub-engineer"),
        max_tokens=1200,
        max_turns=8,
    )


def _remember_sub(
    sub: dict[str, Any],
    *,
    pending: str = "",
    assistant: str = "",
    phase: str = "",
) -> dict[str, Any]:
    extra = "\n".join(
        part
        for part in (
            str(sub.get("offered_api") or "")[:600],
            str(sub.get("resume_instructions") or "")[:400],
        )
        if part
    )
    sub["discussion_digest"] = refresh_discussion_digest(
        str(sub.get("discussion_digest") or ""),
        pending=pending,
        assistant=assistant,
        phase=phase or str(sub.get("status") or "chat"),
        extra=extra,
    )
    return sub


def _prompt_mode(status: str, can_approve: bool) -> str:
    if status == "executing":
        return "executing"
    if status == "paused":
        return "paused"
    if status == "blocked":
        return "blocked"
    if status == "shipped":
        return "shipped"
    if status in {"ready", "suspended"}:
        return "handoff"
    if can_approve:
        return "step"
    return "step"


def _close(message: str, *, status: str, can_approve: bool) -> str:
    return with_next_prompt(
        message,
        approve_label=APPROVE_LABELS.get(status, "Approve"),
        can_approve=can_approve,
        mode=_prompt_mode(status, can_approve),
    )


def _can_approve(sub: dict[str, Any]) -> bool:
    return bool(decorate_sub(sub).get("can_approve"))


def _close_sub(sub: dict[str, Any], message: str) -> str:
    status = str(sub.get("status") or "")
    return _close(message, status=status, can_approve=_can_approve(sub))


def _issue_instructions(sub: dict[str, Any]) -> str:
    issue = sub.get("block_issue") if isinstance(sub.get("block_issue"), dict) else {}
    return str(sub.get("resume_instructions") or issue.get("instructions") or "").strip()


def _taken_workspace_names(session: FleetSession, sub_id: str) -> set[str]:
    taken: set[str] = set()
    for other in session.sub_agents:
        if str(other.get("sub_agent_id")) == sub_id:
            continue
        if str(other.get("status")) == "suspended":
            continue
        taken.add(
            private_dir_name(
                str(other.get("microservice_name") or ""),
                microservice_id=str(other.get("microservice_id") or ""),
            )
        )
    return taken


def empty_sub(
    *,
    design_session_id: str,
    microservice_id: str,
    microservice_name: str,
) -> dict[str, Any]:
    sid = sub_agent_id(design_session_id, microservice_id)
    return {
        "sub_agent_id": sid,
        "design_session_id": design_session_id,
        "microservice_id": microservice_id or "app",
        "microservice_name": microservice_name or "app",
        "plan_spec": "",
        "entity_relationships": "",
        "feature_spec": "",
        "bug_spec": "",
        "tech_stack": "",
        "offered_api": "",
        "implementation_notes": "",
        "peer_consults": [],
        "incoming_api_requests": [],
        "status": "planning",
        "messages": [],
        "search_notes": "",
        "execution_plan": {},
        "previous_plan_spec": "",
        "previous_execution_plan": {},
        "workspace_path": "",
        "workspace_error": "",
        "git_ship_status": "",
        "git_ship_error": "",
        "interrupted_from": "",
        "block_issue": {},
        "resume_instructions": "",
        "discussion_digest": "",
    }


def decorate_sub(sub: dict[str, Any]) -> dict[str, Any]:
    out = dict(sub)
    status = str(out.get("status") or "")
    suspended = status == "suspended"
    plan = out.get("execution_plan") if isinstance(out.get("execution_plan"), dict) else {}
    has_items = has_plan_items(plan)
    blocked = status == "blocked"
    can_approve = ((status == "awaiting_plan" and has_items) or blocked) and not suspended
    out["can_approve"] = can_approve
    if blocked:
        out["approve_kind"] = "blocked"
        out["approve_label"] = APPROVE_LABELS["blocked"]
    elif can_approve:
        out["approve_kind"] = "awaiting_plan"
        out["approve_label"] = APPROVE_LABELS["awaiting_plan"]
    else:
        out["approve_kind"] = ""
        out["approve_label"] = ""
    out["can_pause"] = status == "executing" and not suspended
    out["can_execute"] = status == "paused" and has_items and not suspended
    out["plan_locked"] = status in {"executing", "blocked"} and not suspended
    out["discussion_open"] = not suspended
    issue = out.get("block_issue") if isinstance(out.get("block_issue"), dict) else {}
    out["block_issue"] = issue if blocked and issue else None
    from engineer_agent.workflow import sub_workflow_tiles

    out["workflow"] = sub_workflow_tiles(out)
    msgs = out.get("messages") or []
    out["messages"] = [
        {**m, "content": fold_to_ascii(str(m.get("content") or ""))} if isinstance(m, dict) else m
        for m in msgs
    ]
    return out


def bind_sub_workflow(sub: dict[str, Any]) -> None:
    from engineer_agent.workflow import set_workflow_position

    wf = decorate_sub(sub).get("workflow") or {}
    set_workflow_position(str(wf.get("title") or sub.get("status") or "plan"))


@dataclass
class FleetSession:
    design_session_id: str
    created_at: str
    updated_at: str
    design_version: int = 0
    sub_agents: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    git_repo_url: str = ""
    git_ssh_private_key: str = ""
    git_received_at: str = ""

    def find(self, microservice_id: str | None = None, *, sub_id: str = "") -> dict[str, Any] | None:
        if sub_id:
            for sub in self.sub_agents:
                if str(sub.get("sub_agent_id")) == sub_id:
                    return sub
        want = sub_agent_id(self.design_session_id, microservice_id)
        for sub in self.sub_agents:
            if str(sub.get("sub_agent_id")) == want:
                return sub
        if microservice_id:
            for sub in self.sub_agents:
                if str(sub.get("microservice_id")) == microservice_id:
                    return sub
            name = microservice_id.lower()
            for sub in self.sub_agents:
                if str(sub.get("microservice_name") or "").lower() == name:
                    return sub
        return None

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        want = (name or "").strip().lower()
        if not want:
            return None
        for sub in self.sub_agents:
            if str(sub.get("status")) == "suspended":
                continue
            if str(sub.get("microservice_name") or "").lower() == want:
                return sub
        return None

    def replace(self, updated: dict[str, Any]) -> None:
        sid = str(updated.get("sub_agent_id") or "")
        out: list[dict[str, Any]] = []
        found = False
        for sub in self.sub_agents:
            if str(sub.get("sub_agent_id")) == sid:
                out.append(updated)
                found = True
            else:
                out.append(sub)
        if not found:
            out.append(updated)
        self.sub_agents = out

    def to_public(self) -> dict[str, Any]:
        subs = [decorate_sub(s) for s in self.sub_agents]
        return {
            "design_session_id": self.design_session_id,
            "ui_path": f"/sessions/{self.design_session_id}",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "design_version": self.design_version,
            "sub_agents": subs,
            "messages": [
                {**m, "content": fold_to_ascii(str(m.get("content") or ""))} if isinstance(m, dict) else m
                for m in self.messages
            ],
            "git_repo_url": self.git_repo_url,
            "git_key_configured": bool(self.git_ssh_private_key),
            "git_received_at": self.git_received_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FleetSession:
        return cls(
            design_session_id=str(data.get("design_session_id") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            design_version=int(data.get("design_version") or 0),
            sub_agents=list(data.get("sub_agents") or []),
            messages=list(data.get("messages") or []),
            git_repo_url=str(data.get("git_repo_url") or ""),
            git_received_at=str(data.get("git_received_at") or ""),
        )

    def git_data(self) -> dict[str, str] | None:
        """Repo URL + SSH key for sub-engineers that need to push/pull. Not in to_public()."""
        if not self.git_repo_url or not self.git_ssh_private_key:
            return None
        return {
            "repo_url": self.git_repo_url,
            "ssh_private_key": self.git_ssh_private_key,
        }


class SessionStore:
    def __init__(self) -> None:
        self._cache: dict[str, FleetSession] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._workers_guard = threading.Lock()
        self._watchers: dict[str, list[queue.Queue[dict[str, Any] | None]]] = {}
        self._watchers_guard = threading.Lock()
        self.presence = SessionPresence()
        self._load_disk()

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[session_id] = lock
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
                session = FleetSession.from_dict(data)
                if session.design_session_id:
                    self._hydrate_git_secrets(session)
                    self._cache[session.design_session_id] = session
            except Exception:
                logger.exception("Failed to load fleet %s", path)

    def _persist(self, session: FleetSession) -> None:
        session.updated_at = _now()
        path = self._session_path(session.design_session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session.to_public(), indent=2), encoding="utf-8")
        if session.git_repo_url or session.git_ssh_private_key:
            save_git_secrets(
                session.design_session_id,
                git_repo_url=session.git_repo_url,
                ssh_private_key=session.git_ssh_private_key,
            )
        self._cache[session.design_session_id] = session
        self._notify_watchers(session)

    def watch(self, session_id: str) -> queue.Queue[dict[str, Any] | None]:
        """Subscribe to live public snapshots for this fleet (SSE)."""
        watcher: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=8)
        with self._watchers_guard:
            self._watchers.setdefault(session_id, []).append(watcher)
        return watcher

    def unwatch(self, session_id: str, watcher: queue.Queue[dict[str, Any] | None]) -> None:
        with self._watchers_guard:
            rows = self._watchers.get(session_id) or []
            self._watchers[session_id] = [item for item in rows if item is not watcher]
            if not self._watchers[session_id]:
                self._watchers.pop(session_id, None)
        try:
            watcher.put_nowait(None)
        except queue.Full:
            pass

    def _public_payload(self, session: FleetSession, holder_id: str | None = None) -> dict[str, Any]:
        payload = session.to_public()
        payload["interaction"] = self.presence.snapshot(session.design_session_id, holder_id)
        return payload

    def _notify_watchers(self, session: FleetSession) -> None:
        payload = self._public_payload(session)
        with self._watchers_guard:
            watchers = list(self._watchers.get(session.design_session_id) or [])
        for watcher in watchers:
            try:
                watcher.put_nowait(payload)
            except queue.Full:
                try:
                    watcher.get_nowait()
                except queue.Empty:
                    pass
                try:
                    watcher.put_nowait(payload)
                except queue.Full:
                    pass

    def _hydrate_git_secrets(self, session: FleetSession) -> None:
        secrets = load_git_secrets(session.design_session_id)
        if secrets.get("git_repo_url") and not session.git_repo_url:
            session.git_repo_url = secrets["git_repo_url"]
        if secrets.get("ssh_private_key"):
            session.git_ssh_private_key = secrets["ssh_private_key"]

    def _save_inbound(self, markdown: str, parsed: ParsedHandoff) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = f"-{parsed.microservice_id}" if parsed.microservice_id else ""
        path = self._inbound_dir() / f"{parsed.design_session_id}{suffix}-{parsed.action}-{stamp}.md"
        path.write_text(markdown, encoding="utf-8")

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = []
        for session in sorted(self._cache.values(), key=lambda s: s.updated_at, reverse=True):
            live = [s for s in session.sub_agents if s.get("status") != "suspended"]
            rows.append(
                {
                    "design_session_id": session.design_session_id,
                    "design_version": session.design_version,
                    "sub_agent_count": len(live),
                    "updated_at": session.updated_at,
                    "ui_path": f"/sessions/{session.design_session_id}",
                }
            )
        return rows

    def get(self, session_id: str) -> FleetSession:
        session = self._cache.get(session_id)
        if session is None:
            path = self._session_path(session_id)
            if path.is_file():
                session = FleetSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self._hydrate_git_secrets(session)
                self._cache[session_id] = session
        if session is None:
            raise KeyError(session_id)
        return session

    def ingest(self, markdown: str) -> FleetSession:
        parsed = parse_handoff(markdown.strip())
        with self._lock_for(parsed.design_session_id):
            self._save_inbound(markdown.strip(), parsed)
            session = self._cache.get(parsed.design_session_id)
            if session is None:
                now = _now()
                session = FleetSession(
                    design_session_id=parsed.design_session_id,
                    created_at=now,
                    updated_at=now,
                )
            session.design_version = parsed.design_version
            if parsed.action == "suspend":
                self._suspend(session, parsed)
            else:
                self._upsert_plan(session, parsed)
            self._persist(session)
            return session

    def ingest_git(
        self,
        *,
        design_session_id: str,
        git_repo_url: str,
        ssh_private_key: str,
    ) -> FleetSession:
        session_id = (design_session_id or "").strip()
        url = (git_repo_url or "").strip()
        key = (ssh_private_key or "").replace("\r\n", "\n").strip()
        if key and not key.endswith("\n"):
            key += "\n"
        if not session_id:
            raise ValueError("design_session_id is required")
        if not url:
            raise ValueError("git_repo_url is required")
        if not key:
            raise ValueError("ssh_private_key is required")
        with self._lock_for(session_id):
            session = self._cache.get(session_id)
            if session is None:
                try:
                    session = self.get(session_id)
                except KeyError:
                    now = _now()
                    session = FleetSession(
                        design_session_id=session_id,
                        created_at=now,
                        updated_at=now,
                    )
            session.git_repo_url = url
            session.git_ssh_private_key = key
            session.git_received_at = _now()
            self._persist(session)
            logger.info("Stored git access for fleet %s", session_id)
            return session

    def git_data_for(self, session_id: str) -> dict[str, str] | None:
        """For sub-engineers: clone/push credentials for this design session."""
        return self.get(session_id).git_data()

    def chat(self, session_id: str, message: str, *, service_id: str | None = None) -> FleetSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            sub = session.find(service_id)
            if not sub:
                raise KeyError(service_id or session_id)
            if sub.get("status") == "suspended":
                raise PermissionError("That sub-engineer is suspended.")
            text = (message or "").strip()
            if not text:
                raise ValueError("message is required")
            bind_sub_workflow(sub)
            last_asst = ""
            for msg in reversed(sub.get("messages") or []):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    last_asst = str(msg.get("content") or "").strip()
                    if last_asst:
                        break
            status = str(sub.get("status") or "")
            decorated = decorate_sub(sub)
            context = format_classify_context(
                workflow=(
                    f"Engineer current step: {decorated.get('workflow', {}).get('title') or status}. "
                    f"sub-status={status} can_approve={bool(decorated.get('can_approve'))}. "
                    "Stay on this step unless they approved, paused, or asked to execute."
                ),
                last_assistant=last_asst,
            )
            _category, action = classify_user_message(text, context)
            if last_asst:
                consult = consult_user_turn(
                    pending=text,
                    last_assistant=last_asst,
                    keynotes=str(sub.get("discussion_digest") or ""),
                    phase=str(sub.get("status") or "chat"),
                )
                if consult.needs_clarification and action not in {
                    "approve",
                    "pause",
                    "execute",
                }:
                    return self._chat_locked(session, sub, text, consult.clarify_message)
                sub["discussion_digest"] = consult.keynotes
                session.replace(sub)

            if status == "executing":
                if action == "pause":
                    return self._pause_locked(session, sub)
                if action in {"approve", "execute"}:
                    return self._chat_locked(
                        session,
                        sub,
                        text,
                        "The execution plan is already running. Pause it if you need to change it.",
                    )
                if action == "revise" and not _is_peer_data_request(text):
                    raise PermissionError("Pause execution before updating the plan.")
                if _is_peer_data_request(text):
                    return self._chat_apply(session, sub, text, self._handle_peer_data_request(session, dict(sub), text))
                return self._chat_apply(session, sub, text, self._answer(sub, text))

            if action == "pause":
                return self._chat_locked(session, sub, text, "Nothing is executing, so there is nothing to pause.")

            if status == "blocked":
                if action == "approve":
                    return self._approve_locked(session, sub)
                if action == "execute":
                    return self._chat_locked(
                        session,
                        sub,
                        text,
                        "I paused this item on an issue. Chat with instructions if you have "
                        "them, then approve to continue. Execute plan is for after you pause "
                        "to revise the plan.",
                    )
                extra = ""
                if _is_peer_data_request(text):
                    extra = self._handle_peer_data_request(session, dict(sub), text)
                    sub = session.find(str(sub.get("microservice_id") or "")) or sub
                return self._record_block_instructions(session, sub, text, extra=extra)

            if status == "paused" and action == "execute":
                return self._execute_locked(session, sub)

            if decorated.get("can_approve") and action in {"approve", "execute"}:
                return self._approve_locked(session, sub)

            if _is_peer_data_request(text):
                return self._chat_apply(
                    session, sub, text, self._handle_peer_data_request(session, dict(sub), text)
                )

            if status in PLAN_EDITABLE and action == "revise":
                return self._chat_apply(session, sub, text, self._revise_execution_plan(sub, text))

            return self._chat_apply(session, sub, text, self._answer(sub, text))

    def _chat_locked(self, session: FleetSession, sub: dict[str, Any], text: str, assistant: str) -> FleetSession:
        return self._chat_apply(session, sub, text, _close(assistant, status=str(sub.get("status") or ""), can_approve=bool(decorate_sub(sub).get("can_approve"))))

    def _chat_apply(self, session: FleetSession, sub: dict[str, Any], text: str, assistant: str) -> FleetSession:
        updated = dict(sub)
        msgs = list(updated.get("messages") or [])
        msgs.append({"role": "user", "content": text, "node": str(updated.get("status") or "chat")})
        msgs.append({"role": "assistant", "content": assistant, "node": str(updated.get("status") or "chat")})
        updated["messages"] = msgs
        _remember_sub(
            updated,
            pending=text,
            assistant=assistant,
            phase=str(updated.get("status") or "chat"),
        )
        session.replace(updated)
        self._refresh_consults(session)
        self._persist(session)
        return session

    def _record_block_instructions(
        self,
        session: FleetSession,
        sub: dict[str, Any],
        text: str,
        *,
        extra: str = "",
    ) -> FleetSession:
        issue = dict(sub.get("block_issue") if isinstance(sub.get("block_issue"), dict) else {})
        prior = str(issue.get("instructions") or sub.get("resume_instructions") or "").strip()
        result = invoke_json(
            system=(
                "You are the engineer blocked issue advisor.\n"
                f"{_skill_digest()}\n"
                f"{user_message_first_block(text)}"
                "Development is paused on an issue. The user is chatting about how to "
                "resolve it. If they give instructions, store the combined instructions "
                "to follow after they approve to continue. If they only ask a question, "
                "answer it and keep the previous instructions unchanged.\n"
                'Respond ONLY with JSON: {"assistant_message": string, "instructions": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Service: {sub.get('microservice_name')}\n"
                f"Issue kind: {issue.get('kind') or ''}\n"
                f"Issue title: {issue.get('title') or ''}\n"
                f"Issue detail: {issue.get('detail') or ''}\n"
                f"Paused item: {issue.get('item_title') or ''}\n"
                f"Current instructions:\n{prior}\n\n"
                f"Latest user message:\n{text}"
            ),
        )
        instructions = str(result.get("instructions") or prior).strip()
        compact = text.strip().lower()
        asking = "?" in text or compact.startswith(("why", "what", "which", "how", "show", "explain", "list"))
        if not asking and text.strip():
            if not instructions:
                instructions = text.strip()
            elif prior and prior not in instructions and text.strip() not in instructions:
                instructions = f"{prior}\n{text.strip()}".strip()
        issue["instructions"] = instructions
        updated = dict(sub)
        updated["block_issue"] = issue
        updated["resume_instructions"] = instructions
        msgs = list(updated.get("messages") or [])
        note = str(result.get("assistant_message") or "").strip()
        extra_text = (extra or "").strip()
        if extra_text:
            note = f"{extra_text}\n\n{note}".strip() if note else extra_text
        if not note:
            note = (
                "I recorded your instructions for this issue. When you approve to continue, "
                "I will follow them and retry the paused item."
            )
        if instructions and not asking:
            note = (
                f"{note}\n\nI will follow these instructions after you approve to continue:\n"
                f"{instructions[:2000]}"
            )
        msgs.append({"role": "user", "content": text, "node": "blocked"})
        msgs.append(
            {
                "role": "assistant",
                "content": _close(note, status="blocked", can_approve=True),
                "node": "blocked",
            }
        )
        updated["messages"] = msgs
        _remember_sub(updated, pending=text, assistant=note, phase="blocked")
        session.replace(updated)
        self._refresh_consults(session)
        self._persist(session)
        return session

    def approve(self, session_id: str, *, service_id: str | None = None) -> FleetSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            sub = session.find(service_id)
            if not sub:
                raise KeyError(service_id or session_id)
            bind_sub_workflow(sub)
            return self._approve_locked(session, sub)

    def pause(self, session_id: str, *, service_id: str | None = None) -> FleetSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            sub = session.find(service_id)
            if not sub:
                raise KeyError(service_id or session_id)
            bind_sub_workflow(sub)
            return self._pause_locked(session, sub)

    def execute(self, session_id: str, *, service_id: str | None = None) -> FleetSession:
        with self._lock_for(session_id):
            session = self.get(session_id)
            sub = session.find(service_id)
            if not sub:
                raise KeyError(service_id or session_id)
            bind_sub_workflow(sub)
            status = str(sub.get("status") or "")
            if status == "awaiting_plan":
                return self._approve_locked(session, sub)
            if status == "paused":
                return self._execute_locked(session, sub)
            if status == "blocked":
                raise PermissionError(
                    "This sub-engineer is paused on an issue. Approve to continue after it is resolved."
                )
            raise PermissionError("Execute is only available after you approve a plan or pause one that is running.")

    def tick_execution(self, session_id: str, *, service_id: str | None = None) -> FleetSession:
        """Advance one plan item. Tests use this when BACKGROUND_EXECUTE=false."""
        with self._lock_for(session_id):
            session = self.get(session_id)
            sub = session.find(service_id)
            if not sub:
                raise KeyError(service_id or session_id)
            if str(sub.get("status") or "") != "executing":
                raise PermissionError("Nothing is executing on this sub-engineer.")
            self._tick_locked(session, sub)
            self._persist(session)
            return session

    def _approve_locked(self, session: FleetSession, sub: dict[str, Any]) -> FleetSession:
        if sub.get("status") == "suspended":
            raise PermissionError("That sub-engineer is suspended.")
        decorated = decorate_sub(sub)
        if not decorated.get("can_approve"):
            raise PermissionError("Nothing to approve on this sub-engineer yet.")
        updated = dict(sub)
        msgs = list(updated.get("messages") or [])
        if str(updated.get("status") or "") == "blocked":
            msgs.append({"role": "user", "content": "Approve to continue", "node": "continue"})
            updated["messages"] = msgs
            return self._continue_locked(session, updated)
        msgs.append({"role": "user", "content": "Approved", "node": "approve"})
        updated["messages"] = msgs
        return self._start_execute_locked(session, updated, user_note="Approved the execution plan.")

    def _execute_locked(self, session: FleetSession, sub: dict[str, Any]) -> FleetSession:
        if sub.get("status") == "suspended":
            raise PermissionError("That sub-engineer is suspended.")
        if str(sub.get("status") or "") != "paused":
            raise PermissionError("Pause execution before you can start a new plan.")
        if not has_plan_items(sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {}):
            raise PermissionError("There is no execution plan to run.")
        updated = dict(sub)
        msgs = list(updated.get("messages") or [])
        msgs.append({"role": "user", "content": "Execute plan", "node": "execute"})
        updated["messages"] = msgs
        return self._start_execute_locked(session, updated, user_note="Starting the updated execution plan.")

    def _pause_locked(self, session: FleetSession, sub: dict[str, Any]) -> FleetSession:
        if str(sub.get("status") or "") != "executing":
            raise PermissionError("Nothing is executing on this sub-engineer.")
        self._request_stop(str(sub.get("sub_agent_id") or ""))
        updated = dict(sub)
        updated["previous_execution_plan"] = deepcopy(updated.get("execution_plan") or {})
        updated["status"] = "paused"
        msgs = list(updated.get("messages") or [])
        note = (
            "Paused execution. Chat to update the plan, then execute it. I will transition "
            "from the current item progress to the new plan."
        )
        msgs.append({"role": "user", "content": "Pause", "node": "pause"})
        msgs.append(
            {
                "role": "assistant",
                "content": _close(note, status="paused", can_approve=False),
                "node": "pause",
            }
        )
        updated["messages"] = msgs
        _remember_sub(updated, pending="Pause", assistant=note, phase="paused")
        session.replace(updated)
        self._persist(session)
        return session

    def _suspend(self, session: FleetSession, parsed: ParsedHandoff) -> None:
        sub = session.find(parsed.microservice_id)
        if not sub:
            sub = empty_sub(
                design_session_id=parsed.design_session_id,
                microservice_id=parsed.microservice_id,
                microservice_name=parsed.microservice_name,
            )
        self._request_stop(str(sub.get("sub_agent_id") or ""))
        updated = dict(sub)
        updated["status"] = "suspended"
        msgs = list(updated.get("messages") or [])
        note = _close(
            f"Suspended sub-engineer `{updated['sub_agent_id']}` "
            f"({updated.get('microservice_name')}).",
            status="suspended",
            can_approve=False,
        )
        msgs.append({"role": "assistant", "content": note, "node": "suspend"})
        updated["messages"] = msgs
        _remember_sub(updated, assistant=note, phase="suspended")
        session.replace(updated)

    def _upsert_plan(self, session: FleetSession, parsed: ParsedHandoff) -> None:
        existing = session.find(parsed.microservice_id)
        if existing:
            self._request_stop(str(existing.get("sub_agent_id") or ""))
        sub = dict(existing) if existing else empty_sub(
            design_session_id=parsed.design_session_id,
            microservice_id=parsed.microservice_id,
            microservice_name=parsed.microservice_name,
        )
        had_spec = bool(str(sub.get("plan_spec") or "").strip())
        had_plan = has_plan_items(sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {})
        if had_spec or had_plan:
            sub["previous_plan_spec"] = str(sub.get("plan_spec") or "")
            if had_plan:
                sub["previous_execution_plan"] = deepcopy(sub.get("execution_plan") or {})
            sub["interrupted_from"] = str(sub.get("status") or "")
        sub["microservice_name"] = parsed.microservice_name or sub.get("microservice_name") or "Service"
        sub["plan_spec"] = parsed.markdown
        sub["entity_relationships"] = parsed.entity_relationships
        sub["feature_spec"] = parsed.feature_spec
        sub["bug_spec"] = parsed.bug_spec
        sub["tech_stack"] = parsed.tech_stack
        sub["status"] = "awaiting_plan"
        sub["git_ship_status"] = ""
        sub["git_ship_error"] = ""
        sub["block_issue"] = {}
        sub["resume_instructions"] = ""
        bind_sub_workflow(sub)
        self._draft_offered_api(sub)
        self._draft_execution_plan(sub)
        msgs = list(sub.get("messages") or [])
        if had_plan or str(sub.get("interrupted_from") or "") in {"executing", "paused", "blocked"}:
            note = (
                f"Stopped current development for `{sub['sub_agent_id']}` (**{sub['microservice_name']}**). "
                "I compared the new spec with the previous one and drafted a new execution plan "
                "(priority + dependencies). Review it, chat to change it, or approve to execute."
            )
        else:
            note = (
                f"Sub-engineer `{sub['sub_agent_id']}` owns **{sub['microservice_name']}**. "
                "I drafted the offered API and an execution plan ordered by priority and "
                "feature/bug dependencies. Chat to update the plan, or approve it to start coding."
            )
        msgs.append(
            {
                "role": "assistant",
                "content": _close(note, status="awaiting_plan", can_approve=True),
                "node": "ingest",
            }
        )
        sub["messages"] = msgs
        _remember_sub(sub, pending=str(parsed.markdown or "")[:500], assistant=note, phase="ingest")
        session.replace(sub)
        self._refresh_consults(session)

    def _draft_offered_api(self, sub: dict[str, Any]) -> None:
        bind_sub_workflow(sub)
        name = str(sub.get("microservice_name") or "Service")
        result = invoke_json(
            system=(
                "You are the engineer api offer advisor.\n"
                f"{_skill_digest()}\n"
                "Design the API/protocol THIS microservice offers to callers and peers. "
                "This sub-engineer owns that surface. Do not copy the orchestrator plan's "
                "entity map as an API catalog.\n"
                'Respond ONLY with JSON: {"offered_api": string, "assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Focus microservice: {name}\n\n"
                f"Entity relationships:\n{(sub.get('entity_relationships') or '')[:4000]}\n\n"
                f"Features:\n{(sub.get('feature_spec') or '')[:3000]}\n\n"
                f"Bugs:\n{(sub.get('bug_spec') or '')[:2000]}\n\n"
                f"Tech stack:\n{(sub.get('tech_stack') or '')[:1500]}"
            ),
        )
        sub["offered_api"] = str(result.get("offered_api") or sub.get("offered_api") or "")

    def _revise_offered_api(self, sub: dict[str, Any], pending: str) -> str:
        name = str(sub.get("microservice_name") or "Service")
        result = invoke_json(
            system=(
                "You are the engineer api offer advisor.\n"
                f"{_skill_digest()}\n"
                f"{user_message_first_block(pending)}"
                "Revise THIS microservice's offered API from the user's comments.\n"
                'Respond ONLY with JSON: {"offered_api": string, "assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Focus microservice: {name}\n"
                f"Current offered API:\n{sub.get('offered_api') or ''}\n\n"
                f"Latest user message:\n{pending}"
            ),
        )
        spec = str(result.get("offered_api") or "")
        if spec.strip():
            sub["offered_api"] = spec
        msg = str(result.get("assistant_message") or "Updated the offered API.")
        return with_resolution_close(
            msg,
            changed=True,
            approve_label=APPROVE_LABELS.get(str(sub.get("status") or ""), "Approve"),
            can_approve=_can_approve(sub),
            mode=_prompt_mode(str(sub.get("status") or ""), _can_approve(sub)),
        )

    def _draft_execution_plan(self, sub: dict[str, Any]) -> None:
        bind_sub_workflow(sub)
        name = str(sub.get("microservice_name") or "Service")
        prior_plan = sub.get("previous_execution_plan") if isinstance(sub.get("previous_execution_plan"), dict) else {}
        version = int((prior_plan or {}).get("version") or 0) + 1
        prior_bits = ""
        if str(sub.get("previous_plan_spec") or "").strip():
            prior_bits += f"Previous plan spec:\n{(sub.get('previous_plan_spec') or '')[:4000]}\n\n"
        if has_plan_items(prior_plan):
            prior_bits += f"Previous execution plan:\n{json.dumps(prior_plan, indent=2)[:4000]}\n\n"
        result = invoke_json(
            system=(
                "You are the engineer execution planner.\n"
                f"{_skill_digest()}\n"
                "Draft a development plan for THIS microservice. Order items by priority "
                "(lower number first) and encode feature/bug dependencies. Items that need "
                "other services must list them in peer_services.\n"
                "If a previous spec/plan is provided, compare and write a transition.\n"
                "Respond ONLY with JSON: "
                '{"summary": string, "transition": string, "items": [object], "assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Focus microservice: {name}\n\n"
                f"Entity relationships:\n{(sub.get('entity_relationships') or '')[:4000]}\n\n"
                f"Features:\n{(sub.get('feature_spec') or '')[:3000]}\n\n"
                f"Bugs:\n{(sub.get('bug_spec') or '')[:2000]}\n\n"
                f"Tech stack:\n{(sub.get('tech_stack') or '')[:1500]}\n\n"
                f"{prior_bits}"
            ),
        )
        plan = normalize_plan(result, version=version)
        for item in plan["items"]:
            item["status"] = "pending"
        if not plan["summary"]:
            plan["summary"] = str(result.get("summary") or f"Execution plan for {name}")
        sub["execution_plan"] = plan

    def _revise_execution_plan(self, sub: dict[str, Any], pending: str) -> str:
        name = str(sub.get("microservice_name") or "Service")
        current = sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {}
        result = invoke_json(
            system=(
                "You are the engineer plan revision advisor.\n"
                f"{_skill_digest()}\n"
                f"{user_message_first_block(pending)}"
                "Revise the execution plan from the user's comments. Keep item ids when the "
                "work is the same. Do not mark items done unless they already were.\n"
                "Respond ONLY with JSON: "
                '{"summary": string, "transition": string, "items": [object], "assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Focus microservice: {name}\n"
                f"Current execution plan:\n{json.dumps(current, indent=2)[:6000]}\n\n"
                f"Latest user message:\n{pending}"
            ),
        )
        version = int(current.get("version") or 1)
        plan = normalize_plan(result, version=version)
        # Preserve done/skipped from the current plan when titles still match.
        plan = apply_transition(current, plan)
        sub["execution_plan"] = plan
        status = str(sub.get("status") or "")
        msg = str(result.get("assistant_message") or "Updated the execution plan.")
        return with_resolution_close(
            msg,
            changed=True,
            approve_label=APPROVE_LABELS.get(status, "Approve"),
            can_approve=_can_approve(sub),
            mode=_prompt_mode(status, _can_approve(sub)),
        )

    def _start_execute_locked(
        self,
        session: FleetSession,
        sub: dict[str, Any],
        *,
        user_note: str,
    ) -> FleetSession:
        old = sub.get("previous_execution_plan") if isinstance(sub.get("previous_execution_plan"), dict) else {}
        new = sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {}
        plan = apply_transition(old, new)
        sub["execution_plan"] = plan
        sub["git_ship_status"] = ""
        sub["git_ship_error"] = ""
        sub["block_issue"] = {}
        sub["resume_instructions"] = ""
        self._clear_stop(str(sub.get("sub_agent_id") or ""))
        private, err = prepare_workspace(
            session_id=session.design_session_id,
            microservice_id=str(sub.get("microservice_id") or "app"),
            microservice_name=str(sub.get("microservice_name") or "Service"),
            git_data=session.git_data(),
            taken=_taken_workspace_names(session, str(sub.get("sub_agent_id") or "")),
        )
        sub["workspace_path"] = str(private)
        sub["workspace_error"] = err
        if (err or "").strip():
            self._block_locked(
                session,
                sub,
                kind="git_pull",
                title="Could not pull the git repo",
                detail=(
                    "I could not pull the codebase from the remote git repo, so I paused "
                    f"before writing code. {err.strip()}"
                ),
            )
            self._persist(session)
            return session
        sub["status"] = "executing"
        transition = str(plan.get("transition") or "").strip()
        note = user_note
        if transition:
            note = f"{user_note} Transition: {transition}"
        note = (
            f"{note} I will follow this service's execution plan closely: one item at a "
            "time in priority order (feature updates, new features, then bugs). For each "
            "item I add tests and only move on when every test in the workspace passes. "
            "I consult peer sub-engineers for communication contracts when an item depends "
            "on them, and ship to git only after the entire plan is done."
        )
        msgs = list(sub.get("messages") or [])
        msgs.append(
            {
                "role": "assistant",
                "content": _close(note, status="executing", can_approve=False),
                "node": "execute",
            }
        )
        sub["messages"] = msgs
        _remember_sub(sub, pending=user_note, assistant=note, phase="execute")
        session.replace(sub)
        self._persist(session)
        self._maybe_spawn(session.design_session_id, str(sub.get("sub_agent_id") or ""))
        return session

    def _continue_locked(self, session: FleetSession, sub: dict[str, Any]) -> FleetSession:
        instructions = _issue_instructions(sub)
        plan = sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {}
        sub["execution_plan"] = clear_blocked_items(plan)
        sub["resume_instructions"] = instructions
        sub["block_issue"] = {}
        self._clear_stop(str(sub.get("sub_agent_id") or ""))
        private, err = prepare_workspace(
            session_id=session.design_session_id,
            microservice_id=str(sub.get("microservice_id") or "app"),
            microservice_name=str(sub.get("microservice_name") or "Service"),
            git_data=session.git_data(),
            taken=_taken_workspace_names(session, str(sub.get("sub_agent_id") or "")),
        )
        sub["workspace_path"] = str(private)
        sub["workspace_error"] = err
        if (err or "").strip():
            self._block_locked(
                session,
                sub,
                kind="git_pull",
                title="Could not pull the git repo",
                detail=(
                    "The git pull still failed, so I am staying paused. "
                    f"{err.strip()}"
                ),
            )
            self._persist(session)
            return session
        sub["status"] = "executing"
        note = (
            "Continuing the execution plan from the blocked item. I will retry that "
            "item, keep tests green, then move to the next priority item."
        )
        if instructions:
            note = (
                "Continuing with your instructions. I will follow them to resolve this "
                f"issue, then retry the paused item.\n\n{instructions[:2000]}"
            )
        msgs = list(sub.get("messages") or [])
        msgs.append(
            {
                "role": "assistant",
                "content": _close(note, status="executing", can_approve=False),
                "node": "continue",
            }
        )
        sub["messages"] = msgs
        _remember_sub(sub, pending=instructions, assistant=note, phase="continue")
        session.replace(sub)
        self._persist(session)
        self._maybe_spawn(session.design_session_id, str(sub.get("sub_agent_id") or ""))
        return session

    def _block_locked(
        self,
        session: FleetSession,
        sub: dict[str, Any],
        *,
        kind: str,
        title: str,
        detail: str,
        item: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> None:
        updated = dict(sub)
        self._request_stop(str(updated.get("sub_agent_id") or ""))
        if item is not None:
            blocked_item = dict(item)
            blocked_item["status"] = "blocked"
            blocked_item["notes"] = detail
            source = plan if isinstance(plan, dict) else (
                updated.get("execution_plan") if isinstance(updated.get("execution_plan"), dict) else {}
            )
            updated["execution_plan"] = replace_item(source, blocked_item)
        else:
            blocked_item = {}
        kept = _issue_instructions(updated)
        updated["status"] = "blocked"
        updated["resume_instructions"] = kept
        updated["block_issue"] = {
            "kind": kind,
            "title": title,
            "detail": detail,
            "item_id": str(blocked_item.get("id") or ""),
            "item_title": str(blocked_item.get("title") or ""),
            "instructions": kept,
        }
        msgs = list(updated.get("messages") or [])
        msgs.append(
            {
                "role": "assistant",
                "content": _close(
                    f"**Development paused.** {title}\n\n{detail}\n\n"
                    "Chat with me if you have instructions for how to resolve this. "
                    "When you are ready, approve to continue; I will follow those "
                    "instructions (if any) and retry this item. I will not start the "
                    "next plan item until then.",
                    status="blocked",
                    can_approve=True,
                ),
                "node": "blocked",
            }
        )
        updated["messages"] = msgs
        _remember_sub(updated, assistant=str(title), phase="blocked")
        session.replace(updated)

    def _tick_locked(self, session: FleetSession, sub: dict[str, Any]) -> bool:
        bind_sub_workflow(sub)
        updated = dict(sub)
        plan = updated.get("execution_plan") if isinstance(updated.get("execution_plan"), dict) else {}
        nxt = next_runnable_item(plan)
        if nxt is None:
            if all_items_terminal(plan):
                self._ship_locked(session, updated)
                return True
            session.replace(updated)
            return False
        item = dict(nxt)
        resume = str(updated.get("resume_instructions") or "").strip()
        peers_needed = [str(p).strip() for p in (item.get("peer_services") or []) if str(p).strip()]
        if peers_needed:
            item["status"] = "consulting"
            live = [s for s in session.sub_agents if str(s.get("status")) != "suspended"]
            contracts, missing = snapshot_peer_contracts(item, live)
            item["contracts"] = contracts
            if missing and resume:
                for name in missing:
                    contracts[name] = resume
                item["contracts"] = contracts
                item["notes"] = (
                    "Settled communication contracts from your instructions for "
                    + ", ".join(missing)
                )
                missing = []
            if missing:
                names = ", ".join(missing)
                self._block_locked(
                    session,
                    updated,
                    kind="peer_contract",
                    title="Cannot settle a communication contract",
                    detail=(
                        f"Item **{item.get('title')}** needs a communication contract with "
                        f"**{names}**, but that peer spec is not complete yet (or the peer "
                        "sub-engineer cannot settle the contract). I paused this item."
                    ),
                    item=item,
                    plan=plan,
                )
                return True
            if not str(item.get("notes") or "").strip():
                item["notes"] = "Settled communication contracts with " + ", ".join(sorted(contracts))
        item["status"] = "in_progress"
        private = Path(str(updated.get("workspace_path") or ""))
        if not private.is_dir():
            private, err = prepare_workspace(
                session_id=session.design_session_id,
                microservice_id=str(updated.get("microservice_id") or "app"),
                microservice_name=str(updated.get("microservice_name") or "Service"),
                git_data=session.git_data(),
                taken=_taken_workspace_names(session, str(updated.get("sub_agent_id") or "")),
            )
            updated["workspace_path"] = str(private)
            updated["workspace_error"] = err
            if (err or "").strip():
                self._block_locked(
                    session,
                    updated,
                    kind="git_pull",
                    title="Could not pull the git repo",
                    detail=(
                        "I could not pull the codebase from the remote git repo while "
                        f"working on **{item.get('title')}**. {err.strip()}"
                    ),
                    item=item,
                    plan=plan,
                )
                return True
        write_item_work(
            private,
            item=item,
            microservice_name=str(updated.get("microservice_name") or "Service"),
            instructions=resume,
        )
        ok, test_detail = run_workspace_tests(private)
        if not ok:
            self._block_locked(
                session,
                updated,
                kind="tests_failed",
                title="Tests did not pass",
                detail=(
                    f"I added tests for **{item.get('title')}** and ran the workspace "
                    f"suites before moving on. {test_detail}"
                ),
                item=item,
                plan=plan,
            )
            return True
        item["status"] = "done"
        item["notes"] = (
            f"{str(item.get('notes') or '').strip()} {test_detail}".strip()
        )
        updated["resume_instructions"] = ""
        updated["execution_plan"] = replace_item(plan, item)
        if all_items_terminal(updated["execution_plan"]):
            self._ship_locked(session, updated)
            return True
        session.replace(updated)
        return False

    def _ship_locked(self, session: FleetSession, sub: dict[str, Any]) -> None:
        status, err = ship_workspace(
            session_id=session.design_session_id,
            microservice_name=str(sub.get("microservice_name") or "Service"),
            git_data=session.git_data(),
        )
        sub["git_ship_status"] = status
        sub["git_ship_error"] = err
        sub["status"] = "shipped"
        if status == "pushed":
            ship_note = "Shipped the completed plan to the git remote."
        elif status == "failed":
            ship_note = "Finished local work, but could not push to the git remote."
        else:
            ship_note = "Finished the plan in the local workspace. Git push was skipped."
        msgs = list(sub.get("messages") or [])
        msgs.append(
            {
                "role": "assistant",
                "content": _close(
                    f"Completed every item on the execution plan for **{sub.get('microservice_name')}**. {ship_note}",
                    status="shipped",
                    can_approve=False,
                ),
                "node": "ship",
            }
        )
        sub["messages"] = msgs
        _remember_sub(sub, assistant=ship_note, phase="ship")
        session.replace(sub)

    def _maybe_spawn(self, session_id: str, sub_id: str) -> None:
        if not sub_id or not get_settings().background_execute:
            return
        with self._workers_guard:
            existing = self._workers.get(sub_id)
            if existing and existing.is_alive():
                return
            self._pause_events.setdefault(sub_id, threading.Event()).clear()
            thread = threading.Thread(
                target=self._worker_loop,
                args=(session_id, sub_id),
                daemon=True,
                name=f"eng-exec-{sub_id[-16:]}",
            )
            self._workers[sub_id] = thread
            thread.start()

    def _request_stop(self, sub_id: str) -> None:
        if not sub_id:
            return
        with self._workers_guard:
            self._pause_events.setdefault(sub_id, threading.Event()).set()

    def _clear_stop(self, sub_id: str) -> None:
        if not sub_id:
            return
        with self._workers_guard:
            self._pause_events.setdefault(sub_id, threading.Event()).clear()

    def _stop_requested(self, sub_id: str) -> bool:
        with self._workers_guard:
            event = self._pause_events.get(sub_id)
            return bool(event and event.is_set())

    def stop_all_workers(self) -> None:
        with self._workers_guard:
            for event in self._pause_events.values():
                event.set()
            threads = [thread for thread in self._workers.values() if thread.is_alive()]
        for thread in threads:
            thread.join(timeout=3)

    def _worker_loop(self, session_id: str, sub_id: str) -> None:
        while True:
            with self._lock_for(session_id):
                if self._stop_requested(sub_id):
                    try:
                        session = self.get(session_id)
                        sub = session.find(sub_id=sub_id)
                    except KeyError:
                        return
                    if sub and str(sub.get("status") or "") == "executing":
                        paused = dict(sub)
                        paused["previous_execution_plan"] = deepcopy(paused.get("execution_plan") or {})
                        paused["status"] = "paused"
                        session.replace(paused)
                        self._persist(session)
                    return
                try:
                    session = self.get(session_id)
                except KeyError:
                    return
                sub = session.find(sub_id=sub_id)
                if not sub or str(sub.get("status") or "") != "executing":
                    return
                finished = self._tick_locked(session, sub)
                self._persist(session)
                if finished:
                    return
            time.sleep(0.15)

    def _answer(self, sub: dict[str, Any], question: str) -> str:
        bind_sub_workflow(sub)
        last_assistant = ""
        for msg in reversed(sub.get("messages") or []):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_assistant = str(msg.get("content") or "").strip()
                if last_assistant:
                    break
        extra = f"{SUGGESTED_ANSWER_RULES}\n"
        artifacts = (
            f"Sub-agent: {sub.get('sub_agent_id')}\n"
            f"Service: {sub.get('microservice_name')}\n"
            f"Status: {sub.get('status')}\n"
            f"Offered API:\n{(sub.get('offered_api') or '')[:4000]}\n"
            f"Execution plan:\n{json.dumps(sub.get('execution_plan') or {}, indent=2)[:4000]}\n"
            f"Entity relationships:\n{(sub.get('entity_relationships') or '')[:2500]}\n"
            f"Peer consults:\n{json.dumps(sub.get('peer_consults') or [], indent=2)[:2500]}\n"
            f"Last assistant message:\n{last_assistant[:2500]}"
        )
        result = invoke_json(
            system=(
                "You are answering a question about the current sub-engineer.\n"
                f"{user_message_first_block(question)}"
                f"{extra}"
                "Answer from the artifacts. Honor DISCUSSION MEMORY: do not re-open settled "
                "issues or re-suggest rejected solutions from this sub-engineer.\n"
                "Do not ask them to confirm in place of the answer.\n"
                'Respond ONLY with JSON: {"assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(sub)}\n\n"
                f"Current artifacts:\n{artifacts}\n\n"
                f"Latest user message:\n{question}"
            ),
        )
        return _close_sub(
            sub,
            str(result.get("assistant_message") or "I can answer from this sub-engineer's artifacts."),
        )

    def _handle_peer_data_request(self, session: FleetSession, sub: dict[str, Any], text: str) -> str:
        peer = _peer_from_message(session, sub, text)
        if not peer:
            return _close_sub(
                sub,
                "Name the peer core microservice you initiate toward so I can ask its "
                "sub-engineer to add or drop fields on their offered API.",
            )
        result = invoke_json(
            system=(
                "You are the engineer api change advisor.\n"
                f"{_skill_digest()}\n"
                f"{user_message_first_block(text)}"
                "A peer sub-engineer asked this service to provide more or less data. "
                "Update THIS service's offered API accordingly.\n"
                'Respond ONLY with JSON: {"offered_api": string, "assistant_message": string}'
            ),
            user=(
                f"{_discussion_block(peer)}\n\n"
                f"Focus microservice: {peer.get('microservice_name')}\n"
                f"Current offered API:\n{peer.get('offered_api') or ''}\n\n"
                f"Latest user message:\n{text}\n"
                f"From sub-engineer: {sub.get('sub_agent_id')} ({sub.get('microservice_name')})"
            ),
        )
        spec = str(result.get("offered_api") or "")
        if spec.strip():
            peer["offered_api"] = spec
        incoming = list(peer.get("incoming_api_requests") or [])
        incoming.append(
            {
                "from_sub_agent_id": sub.get("sub_agent_id"),
                "from_name": sub.get("microservice_name"),
                "detail": text,
                "at": _now(),
                "status": "applied",
            }
        )
        peer["incoming_api_requests"] = incoming
        peer_msgs = list(peer.get("messages") or [])
        peer_note = _close_sub(
            peer,
            str(result.get("assistant_message") or "Updated offered API for a peer request."),
        )
        peer_msgs.append({"role": "assistant", "content": peer_note, "node": "peer_request"})
        peer["messages"] = peer_msgs
        last_peer = ""
        for msg in reversed(peer_msgs):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg is not peer_msgs[-1]:
                last_peer = str(msg.get("content") or "").strip()
                if last_peer:
                    break
        peer_consult = consult_user_turn(
            pending=text,
            last_assistant=last_peer or peer_note,
            keynotes=str(peer.get("discussion_digest") or ""),
            phase="peer_request",
        )
        if not peer_consult.needs_clarification:
            peer["discussion_digest"] = peer_consult.keynotes
        _remember_sub(peer, pending=text, assistant=peer_note, phase="peer_request")
        session.replace(peer)
        return with_resolution_close(
            f"Asked **{peer.get('microservice_name')}**'s sub-engineer to update its offered "
            f"API. {result.get('assistant_message') or ''}".strip(),
            changed=True,
            approve_label=APPROVE_LABELS.get(str(sub.get("status") or ""), "Approve"),
            can_approve=_can_approve(sub),
            mode=_prompt_mode(str(sub.get("status") or ""), _can_approve(sub)),
        )

    def _refresh_consults(self, session: FleetSession) -> None:
        for raw in list(session.sub_agents):
            if raw.get("status") == "suspended":
                continue
            sub = dict(raw)
            entities = parse_related_entities(str(sub.get("entity_relationships") or ""))
            consults: list[dict[str, Any]] = []
            for entity in entities:
                if entity.kind not in {"core_microservice", "unknown"}:
                    continue
                if entity.kind == "unknown" and not entity.name.lower().endswith("service"):
                    continue
                peer = None
                if entity.microservice_id:
                    peer = session.find(entity.microservice_id)
                if peer is None:
                    peer = session.find_by_name(entity.name)
                if peer and str(peer.get("sub_agent_id")) == str(sub.get("sub_agent_id")):
                    continue
                if not entity.we_initiate:
                    consults.append(
                        {
                            "peer_sub_agent_id": (peer or {}).get("sub_agent_id") or "",
                            "peer_name": entity.name,
                            "peer_microservice_id": entity.microservice_id
                            or (peer or {}).get("microservice_id")
                            or "",
                            "we_initiate": False,
                            "offered_api": "",
                            "status": "they_initiate",
                        }
                    )
                    continue
                if peer and str(peer.get("offered_api") or "").strip():
                    consults.append(
                        {
                            "peer_sub_agent_id": peer.get("sub_agent_id"),
                            "peer_name": peer.get("microservice_name") or entity.name,
                            "peer_microservice_id": peer.get("microservice_id") or entity.microservice_id,
                            "we_initiate": True,
                            "offered_api": peer.get("offered_api"),
                            "status": "consulted",
                        }
                    )
                else:
                    consults.append(
                        {
                            "peer_sub_agent_id": (peer or {}).get("sub_agent_id") or "",
                            "peer_name": entity.name,
                            "peer_microservice_id": entity.microservice_id
                            or (peer or {}).get("microservice_id")
                            or "",
                            "we_initiate": True,
                            "offered_api": "",
                            "status": "pending_peer",
                        }
                    )
            sub["peer_consults"] = consults
            session.replace(sub)


def _is_peer_data_request(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _PEER_NEED_RE) and (
        "service" in t or "peer" in t or "from " in t or "more" in t or "less" in t
    )


def _peer_from_message(session: FleetSession, sub: dict[str, Any], text: str) -> dict[str, Any] | None:
    lower = text.lower()
    for consult in sub.get("peer_consults") or []:
        name = str(consult.get("peer_name") or "")
        if name and name.lower() in lower:
            found = session.find(str(consult.get("peer_microservice_id") or ""), sub_id=str(consult.get("peer_sub_agent_id") or ""))
            if found:
                return found
            return session.find_by_name(name)
    for other in session.sub_agents:
        if other.get("status") == "suspended":
            continue
        name = str(other.get("microservice_name") or "")
        if name and name.lower() in lower and str(other.get("sub_agent_id")) != str(sub.get("sub_agent_id")):
            return other
    return None


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def reset_store() -> None:
    global _store
    if _store is not None:
        _store.stop_all_workers()
    _store = None
