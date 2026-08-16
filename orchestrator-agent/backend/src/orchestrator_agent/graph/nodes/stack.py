from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    answer_current_artifacts,
    approve_label,
    close_after_feedback,
    invoke_json,
    pick_assistant_message,
    replace_service,
    service_focus_system,
    service_focus_user_block,
    skill_digest,
)
from orchestrator_agent.json_util import recover_markdown_field_from_prose
from orchestrator_agent.query_intent import FEEDBACK_RESOLUTION_RULES, is_revision_request
from orchestrator_agent.web_search import perform_web_search


def stack_research_node(state: dict[str, Any]) -> dict[str, Any]:
    topology = str(state.get("topology") or "standalone")
    pending = (state.get("pending_user_feedback") or "").strip()
    if topology == "distributed":
        svc = active_service(state)
        if not svc:
            return {"route": "wait", "wait_kind": "distributed", "phase": "distributed"}
        name = (svc.get("names") or ["Service"])[-1]
        existing_stack = str(svc.get("tech_stack") or "").strip()
        if pending and existing_stack and not is_revision_request(pending):
            assistant = answer_current_artifacts(
                question=pending,
                artifacts=(
                    f"Service: {name}\n"
                    f"API type: {svc.get('api_type') or svc.get('proposed_api_type') or ''}\n"
                    f"API design:\n{(svc.get('api_design') or '')[:4000]}\n"
                    f"Current tech stack:\n{existing_stack[:6000]}"
                ),
                system_extra=service_focus_system(name),
            )
            updated = dict(svc)
            svc_msgs = list(updated.get("messages") or [])
            svc_msgs.append({"role": "assistant", "content": assistant, "node": "stack"})
            updated["messages"] = svc_msgs
            return {
                "services": replace_service(list(state.get("services") or []), updated),
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "phase": "distributed",
                "route": "wait",
                "wait_kind": "distributed",
                "can_approve": False,
                "approve_kind": "",
                "approve_label": "",
                "messages": [{"role": "assistant", "content": assistant, "node": "stack"}],
            }
        subject = f"{name} microservice {svc.get('role_key') or ''}"
    else:
        svc = None
        name = "application"
        existing_stack = str(state.get("tech_stack") or "").strip()
        if pending and existing_stack and not is_revision_request(pending):
            assistant = answer_current_artifacts(
                question=pending,
                artifacts=(
                    f"Standalone application\n"
                    f"Current tech stack:\n{existing_stack[:6000]}\n"
                    f"Package excerpt:\n{(state.get('package_markdown') or '')[:4000]}"
                ),
            )
            return {
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "phase": "stack",
                "route": "wait",
                "wait_kind": "approve_plan",
                "can_approve": True,
                "approve_kind": "approve_plan",
                "approve_label": approve_label("approve_plan"),
                "messages": [{"role": "assistant", "content": assistant, "node": "stack"}],
            }
        subject = "standalone application from the architect design package"

    query = f"popular tech stack development tools {subject}"
    search_text, live = perform_web_search(query, num_results=5)
    if svc is not None:
        system = (
            "You are the orchestrator tech stack advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "Propose a concrete tech stack for THIS microservice only: language, framework, "
            "this service's own data store if it owns one, and build/test tools. "
            "Do not prescribe shared platform infra used by other services.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so in assistant_message.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "tech_stack": string (markdown),\n'
            '  "search_notes": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        )
        user = service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Prior stack for this service:\n{(svc.get('tech_stack') or '(none)')[:1500]}\n\n"
                f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}"
            ),
        )
    else:
        system = (
            "You are the orchestrator tech stack advisor.\n"
            f"{skill_digest()}\n\n"
            "Propose a concrete tech stack (language, framework, data stores, queues, "
            "build/test tools) used by popular similar systems.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so in assistant_message.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "tech_stack": string (markdown),\n'
            '  "search_notes": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        )
        user = (
            f"Subject: {subject}\n"
            f"User feedback:\n{pending or '(none)'}\n"
            f"Prior stack:\n{state.get('tech_stack') or '(none)'}\n"
            f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}\n"
            f"Package excerpt:\n{(state.get('package_markdown') or '')[:8000]}\n"
        )
    result = invoke_json(
        system=system,
        user=user,
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "tech_stack"),
    )
    stack = str(result.get("tech_stack") or "").strip()
    assistant = pick_assistant_message(
        result,
        fallback=(
            f"Updated the tech stack for **{name}**. "
            "Approve this plan or tell me what to change."
            if svc is not None
            else "Updated the tech stack. Approve this plan or tell me what to change."
        ),
        artifact_keys=("tech_stack",),
    )
    if pending:
        prior_stack = (
            str((svc or {}).get("tech_stack") or "")
            if svc is not None
            else str(state.get("tech_stack") or "")
        )
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=stack != prior_stack,
        )
    notes = str(result.get("search_notes") or search_text[:1200])
    msgs = [{"role": "assistant", "content": assistant, "node": "stack"}]
    if svc is not None:
        updated = dict(svc)
        updated["tech_stack"] = stack
        updated["search_notes"] = notes
        updated["status"] = "awaiting_stack"
        svc_msgs = list(updated.get("messages") or [])
        svc_msgs.append({"role": "assistant", "content": assistant, "node": "stack"})
        updated["messages"] = svc_msgs
        return {
            "services": replace_service(list(state.get("services") or []), updated),
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "phase": "distributed",
            "route": "wait",
            "wait_kind": "distributed",
            "can_approve": False,
            "approve_kind": "",
            "approve_label": "",
            "messages": msgs,
        }
    return {
        "tech_stack": stack,
        "search_notes": notes,
        "app_status": "awaiting_stack",
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "phase": "stack",
        "route": "wait",
        "wait_kind": "approve_plan",
        "can_approve": True,
        "approve_kind": "approve_plan",
        "approve_label": approve_label("approve_plan"),
        "messages": msgs,
    }


def emit_plan_node(state: dict[str, Any]) -> dict[str, Any]:
    topology = str(state.get("topology") or "standalone")
    session_id = str(state.get("design_session_id") or "")
    version = int(state.get("design_version") or 1)
    package = state.get("package_markdown") or ""
    if topology == "distributed":
        svc = active_service(state)
        if not svc:
            return {"route": "wait", "wait_kind": "distributed", "phase": "distributed"}
        name = (svc.get("names") or ["Service"])[-1]
        mid = str(svc.get("microservice_id") or "")
        spec = (
            f"# Plan spec\n\n"
            f"- action: `plan`\n"
            f"- design_session_id: `{session_id}`\n"
            f"- design_version: `{version}`\n"
            f"- microservice_id: `{mid}`\n"
            f"- microservice_name: `{name}`\n\n"
            f"## API type\n\n{svc.get('api_type') or ''}\n\n"
            f"## API design\n\n{svc.get('api_design') or ''}\n\n"
            f"## Tech stack\n\n{svc.get('tech_stack') or ''}\n\n"
            f"## System design (architect package)\n\n{package}\n"
        )
        updated = dict(svc)
        updated["plan_spec"] = spec
        updated["status"] = "sent"
        svc_msgs = list(updated.get("messages") or [])
        sent_msg = (
            f"Plan spec for **{name}** queued for the engineer "
            f"(design `{session_id}`, microservice `{mid}`)."
        )
        svc_msgs.append({"role": "assistant", "content": sent_msg, "node": "emit"})
        updated["messages"] = svc_msgs
        action = {
            "action": "plan",
            "design_session_id": session_id,
            "design_version": version,
            "microservice_id": mid,
            "markdown": spec,
        }
        return {
            "services": replace_service(list(state.get("services") or []), updated),
            "pending_engineer_actions": [action],
            "phase": "distributed",
            "route": "wait",
            "wait_kind": "distributed",
            "can_approve": False,
            "messages": [{"role": "assistant", "content": sent_msg, "node": "emit"}],
        }

    spec = (
        f"# Plan spec\n\n"
        f"- action: `plan`\n"
        f"- design_session_id: `{session_id}`\n"
        f"- design_version: `{version}`\n\n"
        f"## Tech stack\n\n{state.get('tech_stack') or ''}\n\n"
        f"## System design (architect package)\n\n{package}\n"
    )
    sent_msg = f"Plan spec queued for the engineer (design `{session_id}`)."
    action = {
        "action": "plan",
        "design_session_id": session_id,
        "design_version": version,
        "microservice_id": None,
        "markdown": spec,
    }
    return {
        "plan_spec": spec,
        "app_status": "sent",
        "pending_engineer_actions": [action],
        "phase": "idle",
        "route": "wait",
        "wait_kind": "idle",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
        "pending_assistant_message": sent_msg,
        "messages": [{"role": "assistant", "content": sent_msg, "node": "emit"}],
    }
