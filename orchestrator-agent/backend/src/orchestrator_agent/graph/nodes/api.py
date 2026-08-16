from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    invoke_json,
    replace_service,
    skill_digest,
)
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
            "Confirm the API type implied by the architect contract, research typical API "
            "styles for similar services, and recommend keep or change.\n"
            "If search notes say LIVE_WEB_SEARCH_UNAVAILABLE, reason from your own knowledge "
            "and say so.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "architect_api_type": string,\n'
            '  "recommended_api_type": string,\n'
            '  "recommendation": "keep" | "change",\n'
            '  "rationale": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"Service name: {name}\n"
            f"Role: {svc.get('role_key')}\n"
            f"Architect contract:\n{svc.get('architect_api_contract') or '(see package)'}\n\n"
            f"User feedback:\n{pending or '(none)'}\n\n"
            f"Web search ({'live' if live else 'unavailable / stub'}):\n{search_text}\n\n"
            f"Package excerpt:\n{(state.get('package_markdown') or '')[:6000]}\n"
        ),
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
    assistant = str(result.get("assistant_message") or "").strip()
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
    updated = research_api_type_for(state, svc, pending)
    assistant = (updated.get("messages") or [{}])[-1].get("content") or ""
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": "api_type"}],
    }


def api_design_propose_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    name = (svc.get("names") or ["Service"])[-1]
    api_type = svc.get("api_type") or svc.get("proposed_api_type") or "REST"
    peers = [
        (s.get("names") or ["peer"])[-1]
        for s in (state.get("services") or [])
        if s.get("status") != "suspended" and s.get("microservice_id") != svc.get("microservice_id")
    ]
    result = invoke_json(
        system=(
            "You are the orchestrator API design proposer.\n"
            f"{skill_digest()}\n\n"
            "Propose a complete API design. Every endpoint MUST include specific business logic, "
            "including interactions with other microservices when relevant.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "api_design": string (markdown),\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"Service name: {name}\n"
            f"Locked API type: {api_type}\n"
            f"Role: {svc.get('role_key')}\n"
            f"Architect contract:\n{svc.get('architect_api_contract')}\n"
            f"Peer services: {', '.join(peers) or '(none)'}\n"
            f"Prior design:\n{svc.get('api_design') or '(none)'}\n"
            f"User feedback:\n{pending or '(none)'}\n"
            f"Package excerpt:\n{(state.get('package_markdown') or '')[:8000]}\n"
        ),
    )
    design = str(result.get("api_design") or "").strip()
    assistant = str(result.get("assistant_message") or "").strip()
    updated = dict(svc)
    updated["api_design"] = design
    updated["status"] = "awaiting_api_design"
    svc_msgs = list(updated.get("messages") or [])
    svc_msgs.append({"role": "assistant", "content": assistant, "node": "api_design"})
    updated["messages"] = svc_msgs
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": "api_design"}],
    }
