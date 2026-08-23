"""Incremental feature/bug spec updates after the first engineer ship."""

from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    answer_current_artifacts,
    append_service_message,
    close_after_feedback,
    close_user_message,
    invoke_json,
    pick_assistant_message,
    replace_service,
    service_focus_system,
    service_focus_user_block,
    skill_digest,
    spec_delta_is_ready,
)
from orchestrator_agent.json_util import recover_markdown_field_from_prose
from orchestrator_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    FULL_PHASE_REFUSAL,
    is_full_phase_request,
    is_revision_request,
)


def _distributed_wait() -> dict[str, Any]:
    return {
        "phase": "distributed",
        "route": "wait",
        "wait_kind": "distributed",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
    }


def _with_message(
    svc: dict[str, Any], assistant: str, *, node: str = "spec_update", pending: str = ""
) -> tuple[dict[str, Any], str]:
    updated = dict(svc)
    closed = close_user_message(assistant, svc=updated)
    updated = append_service_message(updated, closed, node=node, pending=pending)
    return updated, closed


def _finish(
    state: dict[str, Any], svc: dict[str, Any], assistant: str, *, pending: str = ""
) -> dict[str, Any]:
    updated, closed = _with_message(svc, assistant, pending=pending)
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": closed,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": closed, "node": "spec_update"}],
    }


def discuss_spec_update_for(
    state: dict[str, Any], svc: dict[str, Any], pending: str = ""
) -> tuple[dict[str, Any], str]:
    name = (svc.get("names") or ["Service"])[-1]
    existing_features = str(svc.get("feature_spec") or "").strip()
    existing_bugs = str(svc.get("bug_spec") or "").strip()
    next_version = int(svc.get("spec_version") or 0) + 1
    result = invoke_json(
        system=(
            "You are the orchestrator spec update advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "THIS microservice already shipped a plan spec to the engineer. Do not "
            "re-walk entity relationships, the first-pass feature interview, or tech "
            "stack. Do not rewrite those artifacts from scratch.\n"
            "Apply an incremental spec update only: new features, updates to existing "
            "features, new bugs, and updates to existing bugs.\n"
            "Keep prior feature and bug text unless the user asked to change it.\n"
            "Do not start a full-phase update. If they asked to redo relationships or "
            "stack, leave artifacts unchanged and explain that a new architect design "
            "package is required for that.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "feature_spec": string (markdown, full current feature list),\n'
            '  "bug_spec": string (markdown, full current bug list; empty if none),\n'
            '  "spec_changelog": string (markdown bullets of this increment),\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Last shipped spec version: {int(svc.get('spec_version') or 0)}\n"
                f"Next spec version if shipped: {next_version}\n"
                f"Prior agreed features for this service:\n{existing_features or '(none)'}\n\n"
                f"Prior bugs for this service:\n{existing_bugs or '(none)'}\n"
            ),
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "feature_spec"),
    )
    features = str(result.get("feature_spec") or "").strip() or existing_features
    bugs = str(result.get("bug_spec") or "").strip() or existing_bugs
    changelog = str(result.get("spec_changelog") or "").strip()
    updated = dict(svc)
    updated["feature_spec"] = features
    updated["bug_spec"] = bugs
    updated["spec_changelog"] = changelog
    ready = spec_delta_is_ready(updated)
    updated["status"] = "awaiting_spec_update" if ready else "discussing_spec_update"
    assistant = pick_assistant_message(
        result,
        fallback=(
            f"Drafted an incremental spec update for **{name}** (features and bugs). "
            "Confirm, approve, or agree when this increment should go to the engineer, "
            "or tell me what else to change."
        ),
        artifact_keys=("feature_spec", "bug_spec", "spec_changelog"),
    )
    if pending:
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=features != existing_features or bugs != existing_bugs,
        )
    return updated, assistant


def spec_update_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    name = (svc.get("names") or ["Service"])[-1]
    version = int(svc.get("spec_version") or 0)

    if pending and is_full_phase_request(pending):
        kept = dict(svc)
        if str(kept.get("status") or "") not in {
            "awaiting_spec_update",
            "discussing_spec_update",
        }:
            kept["status"] = "sent"
        assistant = close_after_feedback(FULL_PHASE_REFUSAL, pending=pending, changed=False)
        return _finish(state, kept, assistant, pending=pending)

    existing_features = str(svc.get("feature_spec") or "").strip()
    existing_bugs = str(svc.get("bug_spec") or "").strip()
    if pending and (existing_features or existing_bugs) and not is_revision_request(
        pending, str(svc.get("pending_assistant_message") or state.get("pending_assistant_message") or "")
    ):
        assistant = answer_current_artifacts(
            question=pending,
            artifacts=(
                f"Service: {name}\n"
                f"Entity relationships:\n{(svc.get('entity_relationships') or svc.get('api_design') or '')[:4000]}\n"
                f"Current v1 features / functionality:\n{existing_features[:8000]}\n"
                f"Current bugs:\n{existing_bugs[:4000] or '(none)'}\n"
                f"Tech stack:\n{(svc.get('tech_stack') or '')[:3000]}\n"
                f"Last shipped spec version: {version}"
            ),
            system_extra=service_focus_system(name),
            digest=str(svc.get("discussion_digest") or ""),
        )
        return _finish(state, svc, assistant, pending=pending)

    if not pending and str(svc.get("status") or "") == "sent":
        assistant = (
            f"**{name}** already shipped spec version {version or 1} to the engineer. "
            "Describe new or updated features and bugs and I will draft a new spec "
            "version. A full update that re-walks every planning phase waits for a "
            "new architect design package."
        )
        return _finish(state, svc, assistant, pending=pending)

    updated, assistant = discuss_spec_update_for(state, svc, pending)
    return _finish(state, updated, assistant, pending=pending)
