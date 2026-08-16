from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    approve_label,
    invoke_json,
    replace_service,
    skill_digest,
)
from orchestrator_agent.web_search import perform_web_search


def stack_research_node(state: dict[str, Any]) -> dict[str, Any]:
    topology = str(state.get("topology") or "standalone")
    pending = (state.get("pending_user_feedback") or "").strip()
    if topology == "distributed":
        svc = active_service(state)
        if not svc:
            return {"route": "wait", "wait_kind": "distributed", "phase": "distributed"}
        name = (svc.get("names") or ["Service"])[-1]
        subject = f"{name} microservice {svc.get('role_key') or ''}"
    else:
        svc = None
        name = "application"
        subject = "standalone application from the architect design package"

    query = f"popular tech stack development tools {subject}"
    search_text, live = perform_web_search(query, num_results=5)
    result = invoke_json(
        system=(
            "You are the orchestrator tech stack advisor.\n"
            f"{skill_digest()}\n\n"
            "Propose a concrete tech stack (language, framework, data stores, queues, "
            "build/test tools) used by popular similar systems.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so in assistant_message.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "tech_stack": string (markdown),\n'
            '  "search_notes": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"Subject: {subject}\n"
            f"User feedback:\n{pending or '(none)'}\n"
            f"Prior stack:\n{(svc or {}).get('tech_stack') or state.get('tech_stack') or '(none)'}\n"
            f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}\n"
            f"Package excerpt:\n{(state.get('package_markdown') or '')[:8000]}\n"
        ),
    )
    stack = str(result.get("tech_stack") or "").strip()
    assistant = str(result.get("assistant_message") or "").strip()
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
