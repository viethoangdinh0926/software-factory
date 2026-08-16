from __future__ import annotations

import json
import uuid
from typing import Any

from orchestrator_agent.graph.nodes.common import empty_service, invoke_json, replace_service, skill_digest
from orchestrator_agent.graph.nodes.ingest import reset_planning_fields


def extract_services_node(state: dict[str, Any]) -> dict[str, Any]:
    package = state.get("package_markdown") or ""
    extracted = invoke_json(
        system=(
            "You are the orchestrator service extractor.\n"
            f"{skill_digest()}\n\n"
            "Extract the core microservices from the architect design package "
            "(API contracts, diagram, spec). Ignore infra-only boxes (CDN, LB, Kafka) "
            "unless they are product services.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "services": [{"name": string, "role_key": string, "contract_summary": string}],\n'
            '  "assistant_message": string\n'
            "}\n"
            "role_key is a stable kebab-case responsibility (identity, video-catalog), not the display name."
        ),
        user=f"Design package:\n{package[:14000]}\n",
    )
    new_list = extracted.get("services") or []
    if not isinstance(new_list, list) or not new_list:
        new_list = [
            {
                "name": "CoreDomainService",
                "role_key": "core-domain",
                "contract_summary": "Primary domain API from the architect package.",
            }
        ]

    previous = list(state.get("previous_services") or [])
    ingest_kind = str(state.get("ingest_kind") or "first")
    version = int(state.get("design_version") or 1)
    session_id = str(state.get("design_session_id") or "")

    if ingest_kind == "update" and previous:
        match_result = invoke_json(
            system=(
                "You are the orchestrator service matcher.\n"
                "Match NEW services to PREVIOUS ones by responsibility (role_key / contract), "
                "NOT by display name. A renamed service is the same service.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "matches": [{"name": string, "role_key": string, "microservice_id": string, '
                '"contract_summary": string}],\n'
                '  "removed_microservice_ids": [string]\n'
                "}\n"
                "microservice_id is the previous UUID when matched, or empty string if new."
            ),
            user=(
                "PREVIOUS_SERVICES_JSON:\n"
                + json.dumps(
                    [
                        {
                            "microservice_id": s.get("microservice_id"),
                            "names": s.get("names"),
                            "role_key": s.get("role_key"),
                            "status": s.get("status"),
                        }
                        for s in previous
                    ]
                )
                + "\nNEW_SERVICES_JSON:\n"
                + json.dumps(new_list)
            ),
        )
        matches = match_result.get("matches") or []
        removed_ids = [str(x) for x in (match_result.get("removed_microservice_ids") or [])]
        prev_by_id = {str(s.get("microservice_id")): s for s in previous}
        services: list[dict[str, Any]] = []
        used: set[str] = set()
        for item in matches:
            name = str(item.get("name") or "Service")
            role = str(item.get("role_key") or name.lower())
            mid = str(item.get("microservice_id") or "").strip()
            contract = str(item.get("contract_summary") or "")
            if mid and mid in prev_by_id:
                used.add(mid)
                old = prev_by_id[mid]
                names = list(old.get("names") or [])
                if name not in names:
                    names.append(name)
                svc = reset_planning_fields(old)
                svc["names"] = names
                svc["role_key"] = role or old.get("role_key")
                svc["architect_api_contract"] = contract or old.get("architect_api_contract") or ""
                svc["status"] = "planning"
                services.append(svc)
            else:
                svc = empty_service(
                    microservice_id=str(uuid.uuid4()),
                    name=name,
                    role_key=role,
                    contract=contract,
                )
                services.append(svc)
        actions: list[dict[str, Any]] = list(state.get("pending_engineer_actions") or [])
        suspended = []
        for pid in removed_ids:
            old = prev_by_id.get(pid)
            if not old or old.get("status") == "suspended":
                continue
            used.add(pid)
            marked = dict(old)
            marked["status"] = "suspended"
            suspended.append(marked)
            actions.append(
                {
                    "action": "suspend",
                    "design_session_id": session_id,
                    "design_version": version,
                    "microservice_id": pid,
                    "reason": "service_removed",
                }
            )
        # Any previous not matched and not listed removed still counts as removed.
        for pid, old in prev_by_id.items():
            if pid in used or old.get("status") == "suspended":
                continue
            marked = dict(old)
            marked["status"] = "suspended"
            suspended.append(marked)
            actions.append(
                {
                    "action": "suspend",
                    "design_session_id": session_id,
                    "design_version": version,
                    "microservice_id": pid,
                    "reason": "service_removed",
                }
            )
        notice = extracted.get("assistant_message") or "Extracted core microservices."
        if removed_ids:
            notice += f" Suspended removed services: {', '.join(removed_ids[:6])}."
        return {
            "services": services + suspended,
            "pending_engineer_actions": actions,
            "active_service_id": "",
            "phase": "extract",
            "route": "prime_all",
            "messages": [{"role": "assistant", "content": notice, "node": "extract"}],
        }

    services = [
        empty_service(
            microservice_id=str(uuid.uuid4()),
            name=str(item.get("name") or "Service"),
            role_key=str(item.get("role_key") or "service"),
            contract=str(item.get("contract_summary") or ""),
        )
        for item in new_list
        if isinstance(item, dict)
    ]
    notice = str(extracted.get("assistant_message") or "Extracted core microservices.")
    return {
        "services": services,
        "active_service_id": "",
        "phase": "extract",
        "route": "prime_all",
        "messages": [{"role": "assistant", "content": notice, "node": "extract"}],
    }


def prime_all_services_node(state: dict[str, Any]) -> dict[str, Any]:
    """Prime every live microservice so they can be discussed in parallel tiles."""
    from orchestrator_agent.graph.nodes.api import research_api_type_for

    services = list(state.get("services") or [])
    out = list(services)
    for svc in services:
        if svc.get("status") in {"suspended", "sent", "awaiting_api_type", "awaiting_api_design", "awaiting_stack"}:
            continue
        updated = research_api_type_for(state, svc, pending="")
        out = replace_service(out, updated)
    live = [s for s in out if s.get("status") != "suspended"]
    names = ", ".join((s.get("names") or ["service"])[-1] for s in live) or "none"
    notice = (
        f"Opened planning tiles for {len(live)} microservice(s) at once: {names}. "
        "Discuss any tile independently; each can hand off to the engineer when you approve its plan."
    )
    return {
        "services": out,
        "active_service_id": "",
        "phase": "distributed",
        "route": "wait",
        "wait_kind": "distributed",
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
        "pending_assistant_message": notice,
        "messages": [{"role": "assistant", "content": notice, "node": "prime"}],
    }
