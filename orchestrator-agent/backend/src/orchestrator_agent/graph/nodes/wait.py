from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from orchestrator_agent.graph.nodes.common import (
    active_service,
    approve_label,
    close_user_message,
    decorate_service,
    replace_service,
    spec_delta_is_ready,
)
from orchestrator_agent.query_intent import promote_chat_to_approve


def _append_service_user(state: dict[str, Any], service_id: str, content: str) -> list[dict[str, Any]]:
    services = list(state.get("services") or [])
    for svc in services:
        if str(svc.get("microservice_id")) != service_id:
            continue
        updated = dict(svc)
        msgs = list(updated.get("messages") or [])
        msgs.append({"role": "user", "content": content, "node": str(svc.get("status") or "chat")})
        updated["messages"] = msgs
        return replace_service(services, updated)
    return services


def wait_node(state: dict[str, Any]) -> dict[str, Any]:
    wait_kind = str(state.get("wait_kind") or "idle")
    topology = str(state.get("topology") or "unset")
    session_can_approve = wait_kind not in {"", "idle", "distributed", "discuss_features"}
    assistant = close_user_message(
        state.get("pending_assistant_message") or "",
        approve_kind=wait_kind if session_can_approve else "",
        can_approve=session_can_approve,
        mode="idle" if wait_kind in {"", "idle", "distributed"} else "step",
    )
    resume = interrupt(
        {
            "phase": state.get("phase") or wait_kind,
            "wait_kind": wait_kind,
            "assistant_message": assistant,
            "topology": topology,
            "architect_track": state.get("architect_track") or "unset",
            "design_version": state.get("design_version") or 0,
            "active_service_id": state.get("active_service_id") or "",
            "can_approve": session_can_approve,
            "approve_kind": wait_kind if session_can_approve else "",
            "approve_label": approve_label(wait_kind) if session_can_approve else "",
            "discussion_locked": topology == "standalone" and str(state.get("app_status") or "") == "sent",
        }
    )
    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    service_id = str((resume or {}).get("service_id") or "").strip()
    msgs: list[dict[str, Any]] = []
    services = list(state.get("services") or [])

    if action == "session_done":
        return {
            "phase": "done",
            "route": "end",
            "finalized": True,
            "can_approve": False,
            "messages": msgs
            + [{"role": "assistant", "content": "Session ended.", "node": "done"}],
        }

    if action == "ingest" and user_text:
        return {
            "pending_package": user_text,
            "phase": "ingest",
            "route": "ingest",
            "wait_kind": "",
            "messages": msgs,
        }

    if topology == "distributed" or wait_kind == "distributed":
        if not service_id:
            if action == "chat" and user_text:
                note = close_user_message(
                    "Use a microservice tile to discuss that service’s plan.",
                    mode="idle",
                    can_approve=False,
                )
                return {
                    "pending_assistant_message": note,
                    "route": "wait",
                    "wait_kind": "distributed",
                    "can_approve": False,
                    "messages": msgs
                    + [
                        {"role": "user", "content": user_text, "node": "distributed"},
                        {"role": "assistant", "content": note, "node": "distributed"},
                    ],
                }
            return {"route": "wait", "wait_kind": "distributed", "can_approve": False, "messages": msgs}

        patched_preview = dict(state)
        patched_preview["active_service_id"] = service_id
        preview = active_service(patched_preview)
        tile_can_approve = bool(
            preview and decorate_service(preview).get("can_approve")
        )
        action = promote_chat_to_approve(action, user_text, can_approve=tile_can_approve)

        if user_text and action == "chat":
            services = _append_service_user(state, service_id, user_text)
        elif action == "approve":
            services = _append_service_user(state, service_id, user_text or "Approved")

        patched = dict(state)
        patched["services"] = services
        patched["active_service_id"] = service_id
        svc = active_service(patched)
        if not svc or svc.get("status") == "suspended":
            note = "That microservice is not available for planning."
            return {
                "services": services,
                "active_service_id": service_id,
                "route": "wait",
                "wait_kind": "distributed",
                "messages": msgs + [{"role": "assistant", "content": note, "node": "distributed"}],
            }
        status = str(svc.get("status") or "planning")

        if action == "chat":
            chat_route = {
                "planning": "relations",
                "awaiting_relations": "relations",
                "awaiting_comms": "relations",
                "awaiting_api_type": "relations",
                "awaiting_api_design": "relations",
                "discussing_features": "feature_discuss",
                "awaiting_features": "feature_discuss",
                "awaiting_stack": "stack_research",
                "sent": "spec_update",
                "approved": "spec_update",
                "discussing_spec_update": "spec_update",
                "awaiting_spec_update": "spec_update",
            }.get(status, "wait")
            return {
                "services": services,
                "active_service_id": service_id,
                "pending_user_feedback": user_text,
                "route": chat_route,
                "wait_kind": "distributed",
                "messages": msgs,
            }

        if action == "approve":
            if status in {
                "planning",
                "awaiting_relations",
                "awaiting_comms",
                "awaiting_api_type",
                "awaiting_api_design",
            }:
                updated = dict(svc)
                updated["status"] = "planning"
                return {
                    "services": replace_service(services, updated),
                    "active_service_id": service_id,
                    "route": "feature_discuss",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            if status in {"discussing_features", "awaiting_features"}:
                return {
                    "services": services,
                    "active_service_id": service_id,
                    "route": "stack_research",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            if status == "awaiting_stack":
                return {
                    "services": services,
                    "active_service_id": service_id,
                    "route": "emit_plan",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            if status == "awaiting_spec_update":
                if not spec_delta_is_ready(svc):
                    note = close_user_message(
                        "There is no spec update to ship yet. Describe new or updated "
                        "features and bugs first. A full-phase re-walk waits for a new "
                        "architect design package.",
                        svc=svc,
                    )
                    return {
                        "services": services,
                        "active_service_id": service_id,
                        "route": "wait",
                        "wait_kind": "distributed",
                        "messages": msgs
                        + [{"role": "assistant", "content": note, "node": "distributed"}],
                    }
                return {
                    "services": services,
                    "active_service_id": service_id,
                    "route": "emit_plan",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            note = close_user_message(
                "Chat on this tile to add or update features and bugs, then confirm so "
                "I can ship a new spec version. A full update that re-walks every "
                "planning phase waits for a new architect design package.",
                mode="handoff",
                can_approve=False,
            )
            return {
                "services": services,
                "active_service_id": service_id,
                "route": "wait",
                "wait_kind": "distributed",
                "messages": msgs + [{"role": "assistant", "content": note, "node": "distributed"}],
            }

        return {
            "services": services,
            "active_service_id": service_id,
            "route": "wait",
            "wait_kind": "distributed",
            "messages": msgs,
        }

    action = promote_chat_to_approve(action, user_text, can_approve=session_can_approve)

    if user_text and action == "chat":
        msgs.append({"role": "user", "content": user_text, "node": wait_kind or "chat"})
    if action == "approve":
        msgs.append({"role": "user", "content": user_text or "Approved", "node": wait_kind or "approve"})

    if action == "chat":
        if wait_kind == "idle":
            note = close_user_message(
                "No active planning step. Waiting for the next architect package, or end the session.",
                mode="idle",
                can_approve=False,
            )
            return {
                "pending_assistant_message": note,
                "route": "wait",
                "wait_kind": "idle",
                "can_approve": False,
                "messages": msgs + [{"role": "assistant", "content": note, "node": "idle"}],
            }
        chat_route = {
            "confirm_topology": "classify",
            "approve_features": "feature_discuss",
            "discuss_features": "feature_discuss",
            "decide_api_type": "relations",
            "approve_api_design": "relations",
            "approve_plan": "stack_research",
        }.get(wait_kind, "wait")
        return {
            "pending_user_feedback": user_text,
            "route": chat_route,
            "messages": msgs,
        }

    if action == "approve":
        if wait_kind == "confirm_topology":
            return {
                "topology_certain": True,
                "route": "handle_update",
                "wait_kind": "",
                "messages": msgs,
            }
        if wait_kind == "approve_features" or wait_kind == "discuss_features":
            return {"route": "stack_research", "wait_kind": "", "messages": msgs}
        if wait_kind == "approve_plan":
            return {"route": "emit_plan", "wait_kind": "", "messages": msgs}

    return {"route": "wait", "messages": msgs}
