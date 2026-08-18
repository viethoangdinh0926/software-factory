from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from orchestrator_agent.graph.nodes.common import active_service, approve_label, replace_service


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
    assistant = state.get("pending_assistant_message") or ""
    session_can_approve = wait_kind not in {"", "idle", "distributed", "discuss_features"}
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
                note = "Use a microservice tile to discuss that service’s plan."
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

        if user_text and action == "chat":
            services = _append_service_user(state, service_id, user_text)
        elif action == "approve":
            services = _append_service_user(state, service_id, "Approved")

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
                "planning": "feature_discuss",
                "discussing_features": "feature_discuss",
                "awaiting_features": "feature_discuss",
                "awaiting_api_type": "api_type_research",
                "awaiting_api_design": "api_design_propose",
                "awaiting_stack": "stack_research",
                "sent": "api_design_propose",
                "approved": "api_design_propose",
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
            if status == "awaiting_features":
                return {
                    "services": services,
                    "active_service_id": service_id,
                    "route": "api_type_research",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            if status == "awaiting_api_type":
                updated = dict(svc)
                updated["api_type"] = updated.get("proposed_api_type") or updated.get("api_type") or "REST"
                updated["status"] = "planning"
                return {
                    "services": replace_service(services, updated),
                    "active_service_id": service_id,
                    "route": "api_design_propose",
                    "wait_kind": "distributed",
                    "messages": msgs,
                }
            if status == "awaiting_api_design":
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
            note = "Chat on this tile to revise the API design, then approve through a new engineer handoff."
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

    if user_text and action == "chat":
        msgs.append({"role": "user", "content": user_text, "node": wait_kind or "chat"})
    if action == "approve":
        msgs.append({"role": "user", "content": "Approved", "node": wait_kind or "approve"})

    if action == "chat":
        if wait_kind == "idle":
            note = (
                "No active planning step. Waiting for the next architect package, or end the session."
                if str(state.get("app_status") or "") == "sent"
                else "No active planning step. Waiting for the next architect package, or end the session."
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
            "decide_api_type": "api_type_research",
            "approve_api_design": "api_design_propose",
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
        if wait_kind == "approve_features":
            return {"route": "stack_research", "wait_kind": "", "messages": msgs}
        if wait_kind == "approve_plan":
            return {"route": "emit_plan", "wait_kind": "", "messages": msgs}

    return {"route": "wait", "messages": msgs}
