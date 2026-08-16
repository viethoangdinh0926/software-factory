"""Shared LLM helpers and service-list utilities."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from orchestrator_agent.json_util import parse_llm_json_object
from orchestrator_agent.llm import get_chat_model

logger = logging.getLogger(__name__)

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`."
)

APPROVE_LABELS = {
    "confirm_topology": "Confirm topology",
    "decide_api_type": "Accept API type",
    "approve_api_design": "Approve API design",
    "approve_plan": "Approve plan",
}

STATUS_APPROVE_KIND = {
    "awaiting_api_type": "decide_api_type",
    "awaiting_api_design": "approve_api_design",
    "awaiting_stack": "approve_plan",
}


def approve_label(kind: str) -> str:
    return APPROVE_LABELS.get(kind, "Approve")


def service_step_kind(status: str) -> str:
    return STATUS_APPROVE_KIND.get(status, "")


def decorate_service(svc: dict[str, Any], *, finalized: bool = False) -> dict[str, Any]:
    status = str(svc.get("status") or "")
    kind = service_step_kind(status)
    suspended = status == "suspended"
    open_disc = (not finalized) and (not suspended)
    out = dict(svc)
    out["can_approve"] = bool(kind) and open_disc
    out["approve_kind"] = kind if out["can_approve"] else ""
    out["approve_label"] = approve_label(kind) if out["can_approve"] else ""
    out["discussion_open"] = open_disc
    return out


def invoke_json(system: str, user: str) -> dict[str, Any]:
    model = get_chat_model()
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    content = _invoke_content(model, messages)
    try:
        return parse_llm_json_object(content)
    except ValueError as first_exc:
        logger.warning("LLM JSON parse failed; retrying once: %s", first_exc)
        retry_messages = [
            SystemMessage(content=system),
            HumanMessage(content=user),
            SystemMessage(content=_RETRY_HINT),
            HumanMessage(content=f"Retry now. Preview of bad reply:\n{content[:900]}"),
        ]
        content2 = _invoke_content(model, retry_messages)
        return parse_llm_json_object(content2)


def _invoke_content(model: Any, messages: list[Any]) -> str:
    response = model.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content or "")


def active_service(state: dict[str, Any]) -> dict[str, Any] | None:
    sid = str(state.get("active_service_id") or "")
    for svc in state.get("services") or []:
        if str(svc.get("microservice_id")) == sid:
            return svc
    return None


def replace_service(services: list[dict[str, Any]], updated: dict[str, Any]) -> list[dict[str, Any]]:
    sid = str(updated.get("microservice_id") or "")
    out: list[dict[str, Any]] = []
    found = False
    for svc in services:
        if str(svc.get("microservice_id")) == sid:
            out.append(updated)
            found = True
        else:
            out.append(svc)
    if not found:
        out.append(updated)
    return out


def empty_service(
    *,
    microservice_id: str,
    name: str,
    role_key: str,
    contract: str,
    names: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "microservice_id": microservice_id,
        "names": names or [name],
        "role_key": role_key,
        "architect_api_contract": contract,
        "api_type": "",
        "api_type_recommendation": "",
        "proposed_api_type": "",
        "api_design": "",
        "tech_stack": "",
        "plan_spec": "",
        "status": "planning",
        "messages": [],
        "search_notes": "",
    }


def skill_digest() -> str:
    from orchestrator_agent.config import get_settings

    path = get_settings().skill_path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "You are the Software Factory Orchestrator."
    return text[:4000]
