from __future__ import annotations

import re
from typing import Any

from orchestrator_agent.graph.nodes.common import (
    active_service,
    answer_current_artifacts,
    close_after_feedback,
    close_user_message,
    comms_are_concrete,
    invoke_json,
    pick_assistant_message,
    replace_service,
    service_focus_system,
    service_focus_user_block,
    skill_digest,
)
from orchestrator_agent.json_util import recover_markdown_field_from_prose
from orchestrator_agent.package_parse import (
    extract_communication_schemes,
    extract_http_endpoints,
    format_agreed_endpoints,
    service_comms_excerpt,
)
from orchestrator_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    is_revision_request,
    wants_endpoint_list,
    with_resolution_close,
)

_META_REPLY_RE = re.compile(
    r"(?i)(finalized|please review|complete api design|send to the engineer|"
    r"approve (it|this|the design)|i have proposed|i have updated)"
)

_PROTOCOL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gRPC", ("grpc",)),
    ("GraphQL", ("graphql",)),
    ("WebSocket", ("websocket", "web socket")),
    ("Kafka pub/sub", ("kafka", "pubsub", "pub/sub", "pub-sub")),
    ("REST", ("rest", "https request/response", "http request")),
    ("stream", ("hls", "dash", "byte-range", "stream-based")),
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


def infer_locked_protocols(*texts: str) -> str:
    """Lock protocol(s) from architect communication schemes; do not invent a competing API type."""
    blob = "\n".join(t or "" for t in texts).lower()
    found: list[str] = []
    for label, keys in _PROTOCOL_HINTS:
        if any(key in blob for key in keys) and label not in found:
            found.append(label)
    return " + ".join(found) or "REST"


def draft_comms_for(
    state: dict[str, Any], svc: dict[str, Any], pending: str = ""
) -> dict[str, Any]:
    """Complete this service's communication spec from architect-chosen protocols."""
    name = (svc.get("names") or ["Service"])[-1]
    names = [str(n) for n in (svc.get("names") or []) if n]
    package = str(state.get("package_markdown") or "")
    architect_comms = service_comms_excerpt(package, names) or extract_communication_schemes(
        package
    )
    existing = str(svc.get("api_design") or "").strip()
    locked = (
        str(svc.get("proposed_api_type") or svc.get("api_type") or "").strip()
        or infer_locked_protocols(architect_comms, str(svc.get("architect_api_contract") or ""))
    )
    if pending:
        lowered = pending.lower()
        for token, label in (
            ("grpc", "gRPC"),
            ("graphql", "GraphQL"),
            ("websocket", "WebSocket"),
            ("kafka", "Kafka pub/sub"),
            ("events", "Kafka pub/sub"),
            ("rest", "REST"),
        ):
            if token in lowered:
                locked = label
                break
    result = invoke_json(
        system=(
            "You are the orchestrator communication advisor.\n"
            f"{skill_digest()}\n\n"
            f"{service_focus_system(name)}\n\n"
            "The architect already chose communication schemes/protocols for this system. "
            "Do NOT independently pick REST vs gRPC vs GraphQL as a competing API-type "
            "recommendation. Lock the protocol(s) implied for THIS service and complete "
            "the spec with the user:\n"
            "- REST → METHOD /path, request/response, status codes, peer calls.\n"
            "- gRPC → service/rpc names, messages, streaming vs unary.\n"
            "- pub/sub → topics/events, producers/consumers, payload fields.\n"
            "- stream → direction, framing, backpressure.\n"
            "Stay inside the architect schemes. Name peer services only as collaborators.\n"
            "Do not start the features/functionality interview in this step.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "locked_protocol": string,\n'
            '  "communication_spec": string (markdown),\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=service_focus_user_block(
            state,
            svc,
            pending=pending,
            extra=(
                f"Architect communication schemes (source of truth for protocol):\n"
                f"{architect_comms[:8000] or '(none — infer from the service sketch)'}\n\n"
                f"Currently locked protocol(s): {locked}\n"
                f"Prior communication spec for this service:\n{existing[:8000] or '(none)'}"
            ),
        ),
        recover_prose=lambda text: recover_markdown_field_from_prose(text, "communication_spec"),
    )
    protocol = str(result.get("locked_protocol") or locked).strip() or locked
    spec = str(result.get("communication_spec") or result.get("api_design") or "").strip() or existing
    if not comms_are_concrete(spec) and existing:
        spec = existing
    assistant = pick_assistant_message(
        result,
        fallback=(
            format_agreed_endpoints(name, extract_http_endpoints(spec, existing))
            if extract_http_endpoints(spec, existing)
            else (
                f"Completed a **{protocol}** communication spec for **{name}** from the "
                "architect schemes. Approve the spec, or tell me what to change."
            )
        ),
        artifact_keys=("communication_spec", "api_design"),
    )
    if pending:
        if _looks_like_meta_recap(assistant):
            listed = extract_http_endpoints(spec, existing)
            if listed:
                assistant = format_agreed_endpoints(name, listed)
        assistant = close_after_feedback(
            assistant,
            pending=pending,
            changed=spec != existing or protocol != str(svc.get("proposed_api_type") or ""),
        )
    updated = dict(svc)
    updated["proposed_api_type"] = protocol
    updated["api_type"] = protocol
    updated["api_design"] = spec
    updated["status"] = "awaiting_comms"
    assistant = close_user_message(assistant, svc=updated)
    svc_msgs = list(updated.get("messages") or [])
    svc_msgs.append({"role": "assistant", "content": assistant, "node": "comms"})
    updated["messages"] = svc_msgs
    return updated


def _answer_from_current_spec(
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
    protocol = str(svc.get("api_type") or svc.get("proposed_api_type") or "")
    return answer_current_artifacts(
        question=pending,
        artifacts=(
            f"Service: {name}\n"
            f"Locked protocol(s): {protocol}\n"
            f"Architect sketch:\n{contract or '(none)'}\n"
            f"Current communication spec (source of truth):\n{existing[:12000]}"
        ),
        system_extra=service_focus_system(name),
    )


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
    assistant = close_user_message(assistant, svc=updated)
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


def comms_spec_node(state: dict[str, Any]) -> dict[str, Any]:
    svc = active_service(state)
    if not svc:
        return {**_distributed_wait(), "phase": "distributed"}
    pending = (state.get("pending_user_feedback") or "").strip()
    existing = str(svc.get("api_design") or "").strip()
    prior_status = str(svc.get("status") or "awaiting_comms")
    if pending and existing and not is_revision_request(pending):
        assistant = _answer_from_current_spec(state, svc, pending, existing)
        return _finish_service_turn(state, svc, assistant=assistant, node="comms")

    updated = draft_comms_for(state, svc, pending)
    assistant = (updated.get("messages") or [{}])[-1].get("content") or ""
    if prior_status == "sent" and not pending:
        updated["status"] = "sent"
    return {
        "services": replace_service(list(state.get("services") or []), updated),
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        **_distributed_wait(),
        "messages": [{"role": "assistant", "content": assistant, "node": "comms"}],
    }


# Checkpoint aliases for older graphs that routed API type / API design as separate nodes.
api_type_research_node = comms_spec_node
api_design_propose_node = comms_spec_node
