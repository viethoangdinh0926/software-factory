from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.types import Command

from architect_agent.a2a.orchestrator import HandoffResult, retry_design_package, send_design_package
from architect_agent.config import get_settings
from architect_agent.graph import build_graph, initial_state
from architect_agent.graph.nodes.common import approve_label, design_step_title
from architect_agent.mermaid_sanitize import sanitize_mermaid
from architect_agent.query_intent import is_advance_request, is_step_approval_message, with_next_prompt

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handoff_retryable(last_handoff: dict[str, Any] | None) -> bool:
    status = str((last_handoff or {}).get("status") or "")
    return status in {"failed", "queued"}


def _legacy_map(data: dict[str, Any]) -> dict[str, Any]:
    """Map pre–principal-architect session JSON onto track/step phases."""
    phase = str(data.get("phase") or "phase0")
    track = str(data.get("design_track") or "").lower()
    step = data.get("design_step")
    if track not in {"unset", "lld", "hld"}:
        track = ""
    if phase in {"phase0", "lld", "hld", "market_research", "done"} and track:
        return {
            "phase": phase,
            "design_track": track,
            "design_step": int(step if step is not None else (0 if phase == "phase0" else 1)),
        }
    if phase == "spec_interview":
        return {"phase": "phase0", "design_track": "unset", "design_step": 0}
    if phase == "system_design":
        # Best-effort: treat legacy design sessions as mid-HLD diagram iteration.
        return {"phase": "hld", "design_track": "hld", "design_step": 4}
    if phase == "market_research":
        return {
            "phase": "market_research",
            "design_track": track or "hld",
            "design_step": int(step if step is not None else 6),
        }
    if phase == "done":
        return {"phase": "done", "design_track": track or "hld", "design_step": int(step or 0)}
    return {"phase": "phase0", "design_track": "unset", "design_step": 0}


@dataclass
class DesignSession:
    session_id: str
    created_at: str
    updated_at: str
    phase: str = "phase0"
    design_track: str = "unset"
    design_step: int = 0
    business_spec: str = ""
    design_diagram: str = ""
    design_justification: str = ""
    tradeoff_ledger: str = ""
    scale_estimates: str = ""
    api_contracts: str = ""
    communication_schemes: str = ""
    fmea_notes: str = ""
    ready_for_design: bool = False
    ready_to_advance: bool = False
    design_ready_to_approve: bool = False
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
        interrupt = self.last_interrupt or {}
        can_approve = bool(interrupt.get("can_approve"))
        if not interrupt:
            can_approve = bool(
                (self.phase == "phase0" and self.ready_to_advance)
                or (self.phase in {"lld", "hld"} and (self.ready_to_advance or self.design_ready_to_approve))
                or (self.phase == "market_research" and self.market_evaluation_done)
            )
        label = str(
            interrupt.get("approve_label")
            or approve_label(
                self.phase,
                self.design_track,
                self.design_step,
                design_ready=self.design_ready_to_approve,
            )
        )
        return {
            "design_session_id": self.session_id,
            "phase": self.phase,
            "design_track": self.design_track,
            "design_step": self.design_step,
            "design_step_title": design_step_title(self.phase, self.design_track, self.design_step),
            "ready_for_design": self.ready_for_design,
            "ready_to_advance": self.ready_to_advance,
            "design_ready_to_approve": self.design_ready_to_approve,
            "spec_approved": self.spec_approved,
            "design_approved": self.design_approved,
            "finalized": self.finalized,
            "can_approve": can_approve and not self.finalized,
            "approve_label": label,
            "approve_kind": interrupt.get("approve_kind") or "",
            "business_spec": self.business_spec,
            "design_diagram": self.design_diagram,
            "design_justification": self.design_justification,
            "tradeoff_ledger": self.tradeoff_ledger,
            "scale_estimates": self.scale_estimates,
            "api_contracts": self.api_contracts,
            "communication_schemes": self.communication_schemes,
            "fmea_notes": self.fmea_notes,
            "market_evaluation_report": self.market_evaluation_report,
            "market_evaluation_grade": self.market_evaluation_grade,
            "market_evaluation_done": self.market_evaluation_done,
            "messages": self.messages,
            "ui_path": f"/sessions/{self.session_id}",
            "updated_at": self.updated_at,
            "design_version": self.design_version,
            "last_handoff": self.last_handoff,
            "can_retry_handoff": _handoff_retryable(self.last_handoff) and not self.finalized,
        }


def _session_from_disk(data: dict[str, Any]) -> DesignSession:
    session_id = data.get("design_session_id") or data.get("session_id")
    if not session_id:
        raise KeyError("session file missing design_session_id")
    mapped = _legacy_map(data)
    return DesignSession(
        session_id=session_id,
        created_at=str(data.get("created_at") or data.get("updated_at") or _now()),
        updated_at=str(data.get("updated_at") or _now()),
        phase=mapped["phase"],
        design_track=mapped["design_track"],
        design_step=int(mapped["design_step"]),
        business_spec=str(data.get("business_spec") or ""),
        design_diagram=str(data.get("design_diagram") or ""),
        design_justification=str(data.get("design_justification") or ""),
        tradeoff_ledger=str(data.get("tradeoff_ledger") or ""),
        scale_estimates=str(data.get("scale_estimates") or ""),
        api_contracts=str(data.get("api_contracts") or ""),
        communication_schemes=str(data.get("communication_schemes") or ""),
        fmea_notes=str(data.get("fmea_notes") or ""),
        ready_for_design=bool(data.get("ready_for_design") or data.get("ready_to_advance")),
        ready_to_advance=bool(data.get("ready_to_advance") or data.get("ready_for_design")),
        design_ready_to_approve=bool(data.get("design_ready_to_approve")),
        spec_approved=bool(data.get("spec_approved")),
        design_approved=bool(data.get("design_approved")),
        messages=list(data.get("messages") or []),
        last_interrupt=data.get("last_interrupt"),
        finalized=bool(data.get("finalized")) or mapped["phase"] == "done",
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
            logger.info(
                "Restored design session %s from disk (phase=%s track=%s step=%s)",
                session_id,
                session.phase,
                session.design_track,
                session.design_step,
            )

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
        if session.phase == "lld":
            return "lld_wait"
        if session.phase == "hld":
            return "hld_wait"
        if session.phase == "phase0":
            return "phase0_wait"
        # Legacy fallbacks already mapped in _legacy_map; keep safe defaults.
        if session.phase == "system_design":
            return "hld_wait"
        if session.phase == "spec_interview":
            return "phase0_wait"
        return "phase0_wait"

    def _active_message_node(self, session: DesignSession) -> str:
        if session.phase in {"lld", "hld", "phase0", "market_research"}:
            return session.phase
        if session.phase == "system_design":
            return "hld"
        return "phase0"

    def _seed_state(self, session: DesignSession, last_assistant: str) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "business_spec": session.business_spec,
            "messages": [],
            "phase": session.phase if session.phase in {"phase0", "lld", "hld", "market_research", "done"} else "phase0",
            "design_track": session.design_track if session.design_track in {"unset", "lld", "hld"} else "unset",
            "design_step": session.design_step,
            "ready_for_design": session.ready_for_design,
            "ready_to_advance": session.ready_to_advance,
            "design_ready_to_approve": session.design_ready_to_approve,
            "spec_approved": session.spec_approved,
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
            "market_evaluation_done": session.market_evaluation_done,
            "tradeoff_ledger": session.tradeoff_ledger,
            "scale_estimates": session.scale_estimates,
            "api_contracts": session.api_contracts,
            "communication_schemes": session.communication_schemes,
            "fmea_notes": session.fmea_notes,
            "resume_after_market": False,
            "stay_on_interrupt": False,
        }

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

        as_node = {
            "phase0_wait": "phase0_classify",
            "lld_wait": "lld_step",
            "hld_wait": "hld_step",
            "market_wait": "market_research",
        }[wait_node]
        seed = self._seed_state(session, last_assistant)
        preserved_messages = list(session.messages)
        try:
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
        if values.get("tradeoff_ledger") is not None:
            session.tradeoff_ledger = str(values.get("tradeoff_ledger") or "")
        if values.get("scale_estimates") is not None:
            session.scale_estimates = str(values.get("scale_estimates") or "")
        if values.get("api_contracts") is not None:
            session.api_contracts = str(values.get("api_contracts") or "")
        if values.get("communication_schemes") is not None:
            session.communication_schemes = str(values.get("communication_schemes") or "")
        if values.get("fmea_notes") is not None:
            session.fmea_notes = str(values.get("fmea_notes") or "")
        if values.get("market_evaluation_report"):
            session.market_evaluation_report = values["market_evaluation_report"]
        if values.get("market_evaluation_grade"):
            session.market_evaluation_grade = values["market_evaluation_grade"]
        if "market_evaluation_done" in values:
            session.market_evaluation_done = bool(values.get("market_evaluation_done"))
        if values.get("design_track"):
            session.design_track = str(values["design_track"])
        if "design_step" in values:
            session.design_step = int(values.get("design_step") or 0)
        session.ready_for_design = bool(values.get("ready_for_design", session.ready_for_design))
        session.ready_to_advance = bool(values.get("ready_to_advance", session.ready_to_advance))
        session.design_ready_to_approve = bool(
            values.get("design_ready_to_approve", session.design_ready_to_approve)
        )
        session.spec_approved = bool(values.get("spec_approved", session.spec_approved))
        session.design_approved = bool(values.get("design_approved", session.design_approved))
        session.phase = values.get("phase", session.phase)

        if preserve_messages is not None:
            session.messages = list(preserve_messages)

        if user_text and action == "chat":
            node = self._active_message_node(session)
            last = session.messages[-1] if session.messages else None
            if not last or last.get("role") != "user" or last.get("content") != user_text:
                session.messages.append({"role": "user", "content": user_text, "node": node})
        
        # Add user message for approve actions to show in UI chat history
        # This applies whether called via API or UI, making the chat history complete
        if action == "approve":
            node = self._active_message_node(session)
            approve_message = (user_text or "").strip() or "Approved to advance to next step"
            last = session.messages[-1] if session.messages else None
            # Only add if the last message isn't already the same approve message
            if not last or last.get("role") != "user" or last.get("content") != approve_message:
                session.messages.append({"role": "user", "content": approve_message, "node": node})

        if isinstance(payload, dict):
            session.last_interrupt = payload
            session.phase = payload.get("phase", session.phase)
            if payload.get("design_track"):
                session.design_track = str(payload["design_track"])
            if "design_step" in payload:
                session.design_step = int(payload.get("design_step") or 0)
            session.ready_for_design = bool(
                payload.get("ready_for_design", session.ready_for_design)
            )
            session.ready_to_advance = bool(
                payload.get("ready_to_advance", session.ready_to_advance)
            )
            session.design_ready_to_approve = bool(
                payload.get("design_ready_to_approve", session.design_ready_to_approve)
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
            if payload.get("tradeoff_ledger") is not None:
                session.tradeoff_ledger = str(payload.get("tradeoff_ledger") or "")
            if payload.get("scale_estimates") is not None:
                session.scale_estimates = str(payload.get("scale_estimates") or "")
            if payload.get("api_contracts") is not None:
                session.api_contracts = str(payload.get("api_contracts") or "")
            if payload.get("communication_schemes") is not None:
                session.communication_schemes = str(payload.get("communication_schemes") or "")
            if payload.get("fmea_notes") is not None:
                session.fmea_notes = str(payload.get("fmea_notes") or "")
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
        logger.info("SessionStore.start() called with markdown length: %d", len(markdown))
        session_id = str(uuid.uuid4())
        logger.info("Generated session_id: %s", session_id)
        session = DesignSession(
            session_id=session_id,
            created_at=_now(),
            updated_at=_now(),
            business_spec=markdown,
            phase="phase0",
            design_track="unset",
            design_step=0,
        )
        self._sessions[session_id] = session
        logger.info("Session object created and stored in memory")

        logger.info("Invoking graph with initial_state")
        try:
            result = self._graph.invoke(
                initial_state(session_id, markdown),
                config=self._config(session_id),
            )
            logger.info("Graph invoke completed successfully")
        except Exception:
            logger.exception("Graph invoke failed")
            raise

        logger.info("Applying graph result to session")
        self._apply_graph_result(session, result)
        logger.info("Session start completed successfully")
        return session

    def resume(self, session_id: str, action: str, text: str = "") -> DesignSession:
        session = self.get(session_id)
        if session.finalized:
            return session

        # Handoff runs after market continue (not on design-version approve entry).
        handoff_after_market = action == "approve" and session.phase == "market_research"

        result = self._graph.invoke(
            Command(resume={"action": action, "text": text}),
            config=self._config(session_id),
        )
        self._apply_graph_result(session, result, user_text=text or None, action=action)

        if handoff_after_market:
            self._publish_design_to_orchestrator(session)

        return session

    def _publish_design_to_orchestrator(self, session: DesignSession) -> HandoffResult:
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
            f"Handoff v{session.design_version} → Orchestrator: {handoff.status}. "
            f"{handoff.detail}"
        )
        if handoff.status in {"failed", "queued"}:
            status_line += (
                " You can retry this same package from the session without another design cycle."
            )
        status_line = with_next_prompt(status_line, mode="handoff", can_approve=False)
        node = "hld" if session.design_track == "hld" else "lld"
        session.messages.append({"role": "assistant", "content": status_line, "node": node})
        self._persist(session)
        return handoff

    def retry_orchestrator_handoff(self, session_id: str) -> DesignSession:
        """Resend the last failed/queued package. Does not advance the design track or version."""
        session = self.get(session_id)
        if session.finalized:
            raise ValueError("Session is finalized.")
        if not _handoff_retryable(session.last_handoff):
            raise ValueError("Nothing to retry; last handoff already sent or no package exists.")
        if session.design_version < 1:
            raise ValueError("No design package has been published yet.")
        last = session.last_handoff or {}
        handoff = retry_design_package(
            session_id=session.session_id,
            version=session.design_version,
            saved_path=str(last.get("path") or "") or None,
            markdown=self.final_design_markdown(session.session_id),
        )
        session.last_handoff = handoff.to_public()
        status_line = (
            f"Retry handoff v{session.design_version} → Orchestrator: {handoff.status}. "
            f"{handoff.detail}"
        )
        if handoff.status in {"failed", "queued"}:
            status_line += (
                " You can retry this same package again without another design cycle."
            )
        status_line = with_next_prompt(status_line, mode="handoff", can_approve=False)
        node = "hld" if session.design_track == "hld" else "lld"
        session.messages.append({"role": "assistant", "content": status_line, "node": node})
        self._persist(session)
        return session

    def chat(self, session_id: str, text: str) -> DesignSession:
        session = self.get(session_id)
        if is_advance_request(text) or (
            session.to_public().get("can_approve") and is_step_approval_message(text)
        ):
            return self.resume(session_id, action="approve", text=text)
        return self.resume(session_id, action="chat", text=text)

    def approve(self, session_id: str) -> DesignSession:
        return self.resume(session_id, action="approve")

    def end_session(self, session_id: str) -> DesignSession:
        return self.resume(session_id, action="session_done")

    def current_spec_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        return session.business_spec

    def market_evaluation_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        report = (session.market_evaluation_report or "").strip()
        if not report:
            raise ValueError("Complete a design-version approve to generate a market evaluation.")
        return report

    def final_design_markdown(self, session_id: str) -> str:
        session = self.get(session_id)
        version = max(session.design_version, 1)
        parts = [
            "# System Design Package\n",
            f"Design session: `{session.session_id}`\n",
            f"Design version: `{version}`\n",
            f"Track: `{session.design_track}` step `{session.design_step}`\n",
            f"Generated: {session.updated_at}\n",
            "\n---\n",
            "## Business Specification\n\n",
            session.business_spec.strip(),
            "\n\n---\n",
        ]
        if session.tradeoff_ledger.strip():
            parts.extend(["## Trade-off Ledger\n\n", session.tradeoff_ledger.strip(), "\n\n---\n"])
        if session.scale_estimates.strip():
            parts.extend(["## Scale Estimates\n\n", session.scale_estimates.strip(), "\n\n---\n"])
        if session.api_contracts.strip():
            parts.extend(["## Core Microservices\n\n", session.api_contracts.strip(), "\n\n---\n"])
        if session.communication_schemes.strip():
            parts.extend(
                ["## Communication Schemes\n\n", session.communication_schemes.strip(), "\n\n---\n"]
            )
        if session.fmea_notes.strip():
            parts.extend(["## FMEA Notes\n\n", session.fmea_notes.strip(), "\n\n---\n"])
        parts.extend(
            [
                "## Design Diagram\n\n",
                "```mermaid\n",
                (session.design_diagram or "").strip(),
                "\n```\n\n",
                "## Design Justification\n\n",
                (session.design_justification or "").strip(),
                "\n",
            ]
        )
        return "".join(parts)


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
