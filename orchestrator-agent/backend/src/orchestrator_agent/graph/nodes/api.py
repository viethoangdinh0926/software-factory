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
    relation_artifact,
    relations_are_concrete,
    replace_service,
    service_focus_system,
    service_focus_user_block,
    skill_digest,
)
from orchestrator_agent.json_util import recover_markdown_field_from_prose
from orchestrator_agent.package_parse import extract_communication_schemes, service_comms_excerpt
from orchestrator_agent.query_intent import FEEDBACK_RESOLUTION_RULES, is_revision_request


def _distributed_wait() -> dict[str, Any]:
    return {
        "phase": "distributed",
        "route": "wait",
        "wait_kind": "distributed",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
    }


def _store_relations(updated: dict[str, Any], spec: str) -> dict[str, Any]:
    updated["entity_relationships"] = spec
    updated["api_design"] = spec
    updated["api_type"] = ""
    updated["proposed_api_type"] = ""
    updated["status"] = "awaiting_relations"
    return updated


def draft_relations_for(
    state: dict[str, Any], svc: dict[str, Any], pending: str = ""
) -> dict[str, Any]:
    """Inventory related entities and who initiates each link. Do not lock protocols/APIs."""
    name = (svc.get("names") or ["Service"])[-1]
    names = [str(n) for n in (svc.get("names") or []) if n]
    package = str(state.get("package_markdown") or "")
    architect_comms = service_comms_excerpt(package, names) or extract_communication_schemes(
        package
    )
    existing = relation_artifact(svc)
    peers = [
        {
            "name": str((s.get("names") or ["peer"])[-1]),
            "microservice_id": str(s.get("microservice_id") or ""),
            "role_key": str(s.get("role_key") or ""),
        }
        for s in (state.get("services") or [])
        if s.get("status") != "suspended"
        and str(s.get("microservice_id") or "") != str(svc.get("microservice_id") or "")
    ]
    result = invoke_json(
        system=(
            "You are the orchestrator relationship advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "For THIS core microservice, determine the set of related entities and describe "
            "each relationship in detail. Entities include: users/clients, other core "
            "microservices, and infra (datastores, brokers, gateways, object storage).\n"
            "For every entity say:\n"
            "- kind: user | core_microservice | infra | external\n"
            "- name (and peer microservice_id when it is another core service)\n"
            "- who initiates (this service vs the other entity)\n"
            "- what data, commands, or events flow, and why\n"
            "Do NOT dictate communication schemes, protocols, METHOD /path catalogs, "
            "gRPC RPCs, or Kafka topic designs. Engineer sub-agents own those later.\n"
            "Do not start the features/functionality interview in this step.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "entity_relationships": string (markdown),\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Peer core microservices in this design:\n{peers!r}\n\n"
                f"Architect communication schemes (context only):\n"
                f"{architect_comms[:8000] or '(none)'}\n\n"
                f"Prior entity relationships for this service:\n{existing[:8000] or '(none)'}"
            ),
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "entity_relationships"),
    )
    spec = (
        str(result.get("entity_relationships") or result.get("api_design") or "").strip()
        or existing
    )
    if not relations_are_concrete(spec) and existing:
        spec = existing
    assistant = pick_assistant_message(
        result,
        fallback=(
            f"Mapped related entities for **{name}** (users, peer services, infra) and who "
            "initiates each relationship. Approve the map, or tell me what to change. "
            "Protocols and APIs are left to engineer sub-agents."
        ),
        artifact_keys=("entity_relationships", "api_design"),
    )
    if pending:
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=spec != existing,
        )
    updated = dict(svc)
    _store_relations(updated, spec)
    assistant = close_user_message(assistant, svc=updated)
    return append_service_message(updated, assistant, node="relations", pending=pending)


draft_comms_for = draft_relations_for


def _answer_from_current_spec(
    state: dict[str, Any], svc: dict[str, Any], pending: str, existing: str
) -> str:
    name = (svc.get("names") or ["Service"])[-1]
    contract = str(svc.get("architect_api_contract") or "")
    return answer_current_artifacts(
        question=pending,
        artifacts=(
            f"Service: {name}\n"
            f"Architect sketch:\n{contract or '(none)'}\n"
            f"Current entity relationships (source of truth):\n{existing[:12000]}"
        ),
        system_extra=service_focus_system(name),
        digest=str(svc.get("discussion_digest") or ""),
    )


def _finish_service_turn(
    state: dict[str, Any],
    svc: dict[str, Any],
    *,
    assistant: str,
    node: str,
    status: str | None = None,
    api_design: str | None = None,
    entity_relationships: str | None = None,
    pending: str = "",
) -> dict[str, Any]:
    updated = dict(svc)
    spec = entity_relationships if entity_relationships is not None else api_design
    if spec is not None:
        updated["entity_relationships"] = spec
        updated["api_design"] = spec
    if status is not None:
        updated["status"] = status
    assistant = close_user_message(assistant, svc=updated)
    updated = append_service_message(updated, assistant, node=node, pending=pending)
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": node}],
    }


def relations_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    existing = relation_artifact(svc)
    prior_status = str(svc.get("status") or "awaiting_relations")
    if pending and existing and not is_revision_request(pending):
        assistant = _answer_from_current_spec(state, svc, pending, existing)
        return _finish_service_turn(
            state, svc, assistant=assistant, node="relations", pending=pending
        )

    updated = draft_relations_for(state, svc, pending)
    assistant = (updated.get("messages") or [{}])[-1].get("content") or ""
    if prior_status == "sent" and not pending:
        updated["status"] = "sent"
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": "relations"}],
    }


comms_spec_node = relations_node
api_type_research_node = relations_node
api_design_propose_node = relations_node
