"""Thorough feature / functionality interview after the communication spec is agreed."""

from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    answer_current_artifacts,
    approve_label,
    close_after_feedback,
    features_are_concrete,
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


def _distributed_wait() -> dict[str, Any]:
    return {
        "phase": "distributed",
        "route": "wait",
        "wait_kind": "distributed",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
    }


def _fallback_feature_spec(name: str, contract: str) -> str:
    sketch = (contract or "").strip() or "the architect sketch for this unit"
    return (
        f"## v1 capabilities for {name}\n\n"
        f"- Honor the architect sketch: {sketch[:400]}\n"
        "- Authenticate and authorize callers before mutating data; reject expired or "
        "revoked credentials with a clear error.\n"
        "- Create the primary resource: validate input, persist the system of record, "
        "and return the created identity.\n"
        "- Read the primary resource by id: return 404 when missing; include the fields "
        "callers need to render or act on it.\n"
        "- Update mutable fields with ownership checks; do not silently clobber concurrent writes.\n"
        "- Soft-delete or deactivate: keep an audit trail and notify collaborators.\n\n"
        "## Out of v1\n\n"
        "- Cross-tenant analytics, multi-region active-active, and unrelated product surfaces.\n\n"
        "## Collaborators\n\n"
        "- Name peer services only when this unit must invoke them; do not design their internals.\n"
    )


def discuss_features_for(
    state: dict[str, Any], svc: dict[str, Any], pending: str = ""
) -> dict[str, Any]:
    name = (svc.get("names") or ["Service"])[-1]
    existing = str(svc.get("feature_spec") or "").strip()
    query = f"{name} {svc.get('role_key') or ''} microservice features capabilities responsibilities"
    search_text, live = perform_web_search(query, num_results=5)
    result = invoke_json(
        system=(
            "You are the orchestrator feature advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "The architect package only sketches this microservice. After the communication "
            "spec is agreed, run a thorough interactive feature interview for "
            "THIS service only.\n"
            "Cover every v1 capability: what it does, who it is for, success and failure "
            "behavior, and what is explicitly out of v1. Name peer services only as "
            "collaborators to invoke.\n"
            "Do not change the locked communication protocol(s) or rewrite the communication "
            "spec in this step. Do not propose a tech stack.\n"
            "If the sketch is thin, ask focused follow-up questions in assistant_message "
            "and set ready_for_features false until v1 is concrete.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "feature_spec": string (markdown),\n'
            '  "ready_for_features": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Locked protocol(s): {svc.get('api_type') or svc.get('proposed_api_type') or '(none)'}\n"
                f"Agreed communication spec:\n{(svc.get('api_design') or '(none)')[:4000]}\n\n"
                f"Prior agreed features for this service:\n{existing or '(none — first pass)'}\n\n"
                f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}"
            ),
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "feature_spec"),
    )
    spec = str(result.get("feature_spec") or "").strip() or existing
    if not spec:
        spec = _fallback_feature_spec(name, str(svc.get("architect_api_contract") or ""))
    status = "awaiting_features" if features_are_concrete(spec) else "discussing_features"
    assistant = pick_assistant_message(
        result,
        fallback=(
            f"Here is a v1 feature list for **{name}**. Walk through each capability with me. "
            "Approve features only when this list is complete, or tell me what to add or drop."
        ),
        artifact_keys=("feature_spec",),
    )
    if pending:
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=spec != existing,
        )
    updated = dict(svc)
    updated["feature_spec"] = spec
    updated["search_notes"] = search_text[:1500]
    updated["status"] = status
    svc_msgs = list(updated.get("messages") or [])
    svc_msgs.append({"role": "assistant", "content": assistant, "node": "features"})
    updated["messages"] = svc_msgs
    return updated


def _standalone_wait(ready: bool) -> dict[str, Any]:
    kind = "approve_features" if ready else "discuss_features"
    return {
        "phase": "features",
        "route": "wait",
        "wait_kind": kind,
        "can_approve": ready,
        "approve_kind": "approve_features" if ready else "",
        "approve_label": approve_label("approve_features") if ready else "",
    }


def feature_discuss_node(state: dict[str, Any]) -> dict[str, Any]:
    topology = str(state.get("topology") or "standalone")
    pending = (state.get("pending_user_feedback") or "").strip()
    if topology == "distributed":
        svc = active_service(state)
        if not svc:
            return {**_distributed_wait(), "phase": "distributed"}
        existing = str(svc.get("feature_spec") or "").strip()
        if pending and existing and not is_revision_request(pending):
            name = (svc.get("names") or ["Service"])[-1]
            assistant = answer_current_artifacts(
                question=pending,
                artifacts=(
                    f"Service: {name}\n"
                    f"Architect sketch:\n{svc.get('architect_api_contract') or '(none)'}\n"
                    f"Locked protocol(s): {svc.get('api_type') or svc.get('proposed_api_type') or ''}\n"
                    f"Communication spec:\n{(svc.get('api_design') or '')[:4000]}\n"
                    f"Current v1 features / functionality:\n{existing[:8000]}"
                ),
                system_extra=service_focus_system(name),
            )
            updated = dict(svc)
            svc_msgs = list(updated.get("messages") or [])
            svc_msgs.append({"role": "assistant", "content": assistant, "node": "features"})
            updated["messages"] = svc_msgs
            return {
                "services": replace_service(list(state.get("services") or []), updated),
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                **_distributed_wait(),
                "messages": [{"role": "assistant", "content": assistant, "node": "features"}],
            }
        updated = discuss_features_for(state, svc, pending)
        assistant = (updated.get("messages") or [{}])[-1].get("content") or ""
        return {
            "services": replace_service(list(state.get("services") or []), updated),
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            **_distributed_wait(),
            "messages": [{"role": "assistant", "content": assistant, "node": "features"}],
        }

    existing = str(state.get("feature_spec") or "").strip()
    if pending and existing and not is_revision_request(pending):
        assistant = answer_current_artifacts(
            question=pending,
            artifacts=(
                "Standalone application\n"
                f"Current v1 features / functionality:\n{existing[:8000]}\n"
                f"Package excerpt:\n{(state.get('package_markdown') or '')[:4000]}"
            ),
        )
        ready = features_are_concrete(existing)
        return {
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "messages": [{"role": "assistant", "content": assistant, "node": "features"}],
            **_standalone_wait(ready),
        }

    query = "standalone application product features capabilities similar systems"
    search_text, live = perform_web_search(query, num_results=5)
    result = invoke_json(
        system=(
            "You are the orchestrator feature advisor.\n"
            f"{skill_digest()}\n\n"
            "The architect package only sketches this stand-alone application. Before any "
            "tech stack, run a thorough interactive feature interview.\n"
            "Cover every v1 capability: what it does, who it is for, success and failure "
            "behavior, and what is explicitly out of v1.\n"
            "Do not propose a tech stack in this step.\n"
            "If the sketch is thin, ask focused follow-up questions and set "
            "ready_for_features false until v1 is concrete.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "feature_spec": string (markdown),\n'
            '  "ready_for_features": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"User feedback:\n{pending or '(none)'}\n"
            f"Prior agreed features:\n{existing or '(none — first pass)'}\n"
            f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}\n"
            f"Package excerpt:\n{(state.get('package_markdown') or '')[:8000]}\n"
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "feature_spec"),
    )
    spec = str(result.get("feature_spec") or "").strip() or existing
    if not spec:
        spec = _fallback_feature_spec("application", "")
    ready = features_are_concrete(spec)
    assistant = pick_assistant_message(
        result,
        fallback=(
            "Here is a v1 feature list for this application. Walk through each capability "
            "with me. Approve features only when this list is complete, or tell me what to change."
        ),
        artifact_keys=("feature_spec",),
    )
    if pending:
        assistant = close_after_feedback(assistant, pending=pending, changed=spec != existing)
    return {
        "feature_spec": spec,
        "search_notes": str(result.get("search_notes") or search_text[:1200]),
        "app_status": "awaiting_features" if ready else "discussing_features",
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "messages": [{"role": "assistant", "content": assistant, "node": "features"}],
        **_standalone_wait(ready),
    }
