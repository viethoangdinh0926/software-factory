from __future__ import annotations

import re
from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    close_after_feedback,
    invoke_json,
    pick_assistant_message,
    replace_service,
    service_focus_system,
    service_focus_user_block,
    skill_digest,
    answer_current_artifacts,
)
from orchestrator_agent.json_util import recover_api_type_from_prose, recover_markdown_field_from_prose
from orchestrator_agent.package_parse import extract_http_endpoints, format_agreed_endpoints
from orchestrator_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    is_revision_request,
    wants_endpoint_list,
    with_resolution_close,
)
from orchestrator_agent.web_search import perform_web_search

_META_REPLY_RE = re.compile(
    r"(?i)(finalized|please review|complete api design|send to the engineer|"
    r"approve (it|this|the design)|i have proposed|i have updated)"
)


def _looks_like_meta_recap(text: str) -> bool:
    body = text or ""
    if not _META_REPLY_RE.search(body):
        return False
    return not extract_http_endpoints(body)


def _distributed_wait() -> dict[str, Any]:
    return {
        "phase": "distributed",
        "route": "wait",
        "wait_kind": "distributed",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
    }


def research_api_type_for(
    state: dict[str, Any], svc: dict[str, Any], pending: str = ""
) -> dict[str, Any]:
    name = (svc.get("names") or ["Service"])[-1]
    query = f"{name} {svc.get('role_key') or ''} microservice API style REST gRPC GraphQL"
    search_text, live = perform_web_search(query, num_results=5)
    result = invoke_json(
        system=(
            "You are the orchestrator API type advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "Confirm the API type implied by THIS service's architect contract, research typical API "
            "styles for similar services of this role, and recommend keep or change.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "architect_api_type": string,\n'
            '  "recommended_api_type": string,\n'
            '  "recommendation": "keep" | "change",\n'
            '  "rationale": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}"
            ),
        ),
        recover_prose=recover_api_type_from_prose,
    )
    recommended = str(result.get("recommended_api_type") or "REST")
    rec = str(result.get("recommendation") or "keep").lower()
    if rec not in {"keep", "change"}:
        rec = "keep"
    if pending:
        lowered = pending.lower()
        for token in ("grpc", "graphql", "rest", "events", "websocket"):
            if token in lowered:
                recommended = {
                    "grpc": "gRPC",
                    "graphql": "GraphQL",
                    "rest": "REST",
                    "events": "events",
                    "websocket": "WebSocket",
                }[token]
                rec = (
                    "change"
                    if recommended.lower() != str(result.get("architect_api_type") or "rest").lower()
                    else "keep"
                )
    assistant = pick_assistant_message(
        result,
        fallback=(
            f"For **{name}** I recommend **{recommended}** ({rec}). "
            "Approve to lock this API type, or tell me what to change."
        ),
        artifact_keys=("rationale",),
    )
    if pending:
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=recommended != str(svc.get("proposed_api_type") or ""),
        )
    updated = dict(svc)
    updated["proposed_api_type"] = recommended
    updated["api_type_recommendation"] = rec
    updated["search_notes"] = search_text[:1500]
    updated["status"] = "awaiting_api_type"
    svc_msgs = list(updated.get("messages") or [])
    svc_msgs.append({"role": "assistant", "content": assistant, "node": "api_type"})
    updated["messages"] = svc_msgs
    return updated


def api_type_research_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    existing_type = str(svc.get("proposed_api_type") or svc.get("api_type") or "").strip()
    if pending and existing_type and not is_revision_request(pending):
        name = (svc.get("names") or ["Service"])[-1]
        assistant = answer_current_artifacts(
            question=pending,
            artifacts=(
                f"Service: {name}\n"
                f"Architect contract:\n{svc.get('architect_api_contract') or '(none)'}\n"
                f"Proposed API type: {existing_type}\n"
                f"Recommendation: {svc.get('api_type_recommendation') or '(none)'}\n"
                f"Agreed features:\n{(svc.get('feature_spec') or '')[:4000]}\n"
                f"Search notes:\n{(svc.get('search_notes') or '')[:1500]}"
            ),
            system_extra=service_focus_system(name),
        )
        return _finish_service_turn(state, svc, assistant=assistant, node="api_type")
    updated = research_api_type_for(state, svc, pending)
    assistant = (updated.get("messages") or [{}])[-1].get("content") or ""
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": "api_type"}],
    }


def _answer_from_current_design(
    state: dict[str, Any], svc: dict[str, Any], pending: str, existing: str
) -> str:
    name = (svc.get("names") or ["Service"])[-1]
    contract = str(svc.get("architect_api_contract") or "")
    endpoints = extract_http_endpoints(existing, contract)
    if wants_endpoint_list(pending):
        return with_resolution_close(
            format_agreed_endpoints(name, endpoints),
            changed=False,
        )
    result = invoke_json(
        system=(
            "You are the orchestrator answering a question about an already-agreed microservice API.\n"
            f"{service_focus_system(name)}\n\n"
            "Answer the user's question in assistant_message using ONLY the current API design.\n"
            "Quote actual HTTP methods and paths when relevant. Do not say you finalized anything. "
            "Do not ask for approval.\n"
            "Set api_design to an empty string (keep the current design).\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "api_design": "",\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=f"Current agreed API design (source of truth):\n{existing[:12000]}",
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "assistant_message"),
    )
    assistant = pick_assistant_message(
        result,
        fallback=format_agreed_endpoints(name, endpoints) if endpoints else "",
        artifact_keys=(),
    )
    if _looks_like_meta_recap(assistant) and endpoints:
        assistant = format_agreed_endpoints(name, endpoints)
    return with_resolution_close(assistant, changed=False)


def _finish_service_turn(
    state: dict[str, Any],
    svc: dict[str, Any],
    *,
    assistant: str,
    node: str,
    status: str | None = None,
    api_design: str | None = None,
) -> dict[str, Any]:
    updated = dict(svc)
    if api_design is not None:
        updated["api_design"] = api_design
    if status is not None:
        updated["status"] = status
    svc_msgs = list(updated.get("messages") or [])
    svc_msgs.append({"role": "assistant", "content": assistant, "node": node})
    updated["messages"] = svc_msgs
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": node}],
    }


def api_design_propose_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    name = (svc.get("names") or ["Service"])[-1]
    existing = str(svc.get("api_design") or "").strip()
    prior_status = str(svc.get("status") or "awaiting_api_design")
    if pending and existing and not is_revision_request(pending):
        assistant = _answer_from_current_design(state, svc, pending, existing)
        return _finish_service_turn(state, svc, assistant=assistant, node="api_design")

    api_type = svc.get("api_type") or svc.get("proposed_api_type") or "REST"
    result = invoke_json(
        system=(
            "You are the orchestrator API design proposer.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "Propose a complete API design for THIS microservice only. Cover the agreed "
            "features / functionality. Every endpoint MUST include "
            "specific business logic. When this service must call a peer, name the peer and the "
            "reason; do not specify the peer's implementation.\n"
            "assistant_message must list the HTTP method and path for every endpoint in this design.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "api_design": string (markdown),\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Locked API type: {api_type}\n"
                f"Agreed features (must be covered):\n{(svc.get('feature_spec') or '(none)')[:6000]}\n"
                f"Prior API design for this service:\n{existing[:8000] or '(none)'}"
            ),
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "api_design"),
    )
    design = str(result.get("api_design") or "").strip() or existing
    assistant = pick_assistant_message(
        result,
        fallback=(
            format_agreed_endpoints(name, extract_http_endpoints(design, existing))
            if extract_http_endpoints(design, existing)
            else (
                f"Updated the API design for **{name}**. "
                "Review it on this tile and approve when it looks right, or tell me what to change."
            )
        ),
        artifact_keys=("api_design",),
    )
    if pending:
        if _looks_like_meta_recap(assistant):
            listed = extract_http_endpoints(design, existing)
            if listed:
                assistant = format_agreed_endpoints(name, listed)
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=design != existing,
        )
    next_status = "awaiting_api_design"
    if prior_status == "sent" and not pending:
        next_status = "sent"
    return _finish_service_turn(
        state,
        svc,
        assistant=assistant,
        node="api_design",
        status=next_status,
        api_design=design,
    )
