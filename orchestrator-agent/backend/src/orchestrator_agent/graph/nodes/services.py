from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from orchestrator_agent.graph.nodes.common import (
    close_user_message,
    empty_service,
    invoke_json,
    replace_service,
    skill_digest,
)
from orchestrator_agent.graph.nodes.ingest import reset_planning_fields
from orchestrator_agent.json_util import recover_extract_from_prose
from orchestrator_agent.package_parse import extract_core_services

logger = logging.getLogger(__name__)

_STEM_RE = re.compile(r"[^a-z0-9]+")


def _stem(value: str) -> str:
    text = _STEM_RE.sub("", (value or "").lower().replace("service", ""))
    return text


def _best_previous_id(
    item: dict[str, Any],
    previous: list[dict[str, Any]],
    used: set[str],
) -> str:
    """Match a newly extracted service to a prior UUID by role or name, not LLM guesswork."""
    role = str(item.get("role_key") or "").lower().strip()
    name = str(item.get("name") or "").strip()
    name_stem = _stem(name)
    role_stem = _stem(role)

    def candidates() -> list[tuple[int, str]]:
        ranked: list[tuple[int, str]] = []
        for prev in previous:
            pid = str(prev.get("microservice_id") or "")
            if not pid or pid in used or prev.get("status") == "suspended":
                continue
            prev_role = str(prev.get("role_key") or "").lower().strip()
            prev_names = [str(n) for n in (prev.get("names") or [])]
            prev_stems = [_stem(n) for n in prev_names if _stem(n)]
            score = 0
            if role and prev_role and role == prev_role:
                score = 100
            elif name and name in prev_names:
                score = 90
            elif role and prev_role and (role in prev_role or prev_role in role) and min(len(role), len(prev_role)) >= 5:
                score = 80
            elif name_stem and any(
                (name_stem in ps or ps in name_stem) and min(len(name_stem), len(ps)) >= 5 for ps in prev_stems
            ):
                score = 70
            elif (
                role_stem
                and _stem(prev_role)
                and (role_stem in _stem(prev_role) or _stem(prev_role) in role_stem)
                and min(len(role_stem), len(_stem(prev_role))) >= 5
            ):
                score = 60
            if score:
                ranked.append((score, pid))
        ranked.sort(reverse=True)
        return ranked

    ranked = candidates()
    return ranked[0][1] if ranked else ""


def _align_update(
    *,
    previous: list[dict[str, Any]],
    new_list: list[Any],
    session_id: str,
    version: int,
    actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Keep prior UUIDs for renamed services; add unmatched new ones; suspend leftovers."""
    used: set[str] = set()
    live: list[dict[str, Any]] = []
    newly_removed: list[str] = []
    for item in new_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Service")
        role = str(item.get("role_key") or name.lower())
        contract = str(item.get("contract_summary") or "")
        mid = _best_previous_id(item, previous, used)
        if mid:
            used.add(mid)
            old = next(s for s in previous if str(s.get("microservice_id")) == mid)
            names = list(old.get("names") or [])
            if name not in names:
                names.append(name)
            svc = reset_planning_fields(old)
            svc["names"] = names
            svc["role_key"] = role or old.get("role_key")
            svc["architect_api_contract"] = contract or old.get("architect_api_contract") or ""
            svc["status"] = "planning"
            live.append(svc)
        else:
            live.append(
                empty_service(
                    microservice_id=str(uuid.uuid4()),
                    name=name,
                    role_key=role,
                    contract=contract,
                )
            )

    suspended: list[dict[str, Any]] = []
    for old in previous:
        pid = str(old.get("microservice_id") or "")
        if old.get("status") == "suspended":
            suspended.append(old)
            continue
        if not pid or pid in used:
            continue
        marked = dict(old)
        marked["status"] = "suspended"
        suspended.append(marked)
        newly_removed.append(pid)
        actions.append(
            {
                "action": "suspend",
                "design_session_id": session_id,
                "design_version": version,
                "microservice_id": pid,
                "reason": "service_removed",
            }
        )
    return live + suspended, actions, newly_removed


def extract_services_node(state: dict[str, Any]) -> dict[str, Any]:
    package = state.get("package_markdown") or ""
    heuristic = extract_core_services(package)
    if heuristic:
        extracted = {
            "services": heuristic,
            "assistant_message": "Extracted core microservices from Core Microservices headings.",
        }
    else:
        extracted = invoke_json(
            system=(
                "You are the orchestrator service extractor.\n"
                f"{skill_digest()}\n\n"
                "Extract the core microservices from the architect design package "
                "(core microservice descriptions, communication schemes, diagram, spec). "
                "Ignore infra-only boxes (CDN, LB, Kafka) "
                "unless they are product services.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "services": [{"name": string, "role_key": string, "contract_summary": string}],\n'
                '  "assistant_message": string\n'
                "}\n"
                "role_key is a stable kebab-case responsibility (identity, video-catalog), not the display name."
            ),
            user=f"Design package:\n{package[:60000]}\n",
            recover_prose=recover_extract_from_prose,
        )
    new_list = extracted.get("services") or []
    if not isinstance(new_list, list):
        new_list = []
    if heuristic:
        new_list = heuristic
    elif not new_list:
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
        actions: list[dict[str, Any]] = list(state.get("pending_engineer_actions") or [])
        services, actions, removed = _align_update(
            previous=previous,
            new_list=new_list,
            session_id=session_id,
            version=version,
            actions=actions,
        )
        notice = str(extracted.get("assistant_message") or "Extracted core microservices.")
        live_n = sum(1 for s in services if s.get("status") != "suspended")
        notice += f" Matching kept {live_n} live service(s)."
        if removed:
            notice += f" Suspended removed services: {', '.join(removed[:6])}."
        return {
            "services": services,
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
    """Prime every live microservice with a communication spec from architect protocols."""
    from orchestrator_agent.graph.nodes.api import draft_comms_for
    from orchestrator_agent.graph.nodes.common import comms_are_concrete

    skip = {
        "suspended",
        "sent",
        "awaiting_comms",
        "discussing_features",
        "awaiting_features",
        "awaiting_api_type",
        "awaiting_api_design",
        "awaiting_stack",
    }
    services = list(state.get("services") or [])
    out = list(services)
    for svc in services:
        if svc.get("status") in skip:
            continue
        name = (svc.get("names") or ["service"])[-1]
        try:
            updated = draft_comms_for(state, svc, pending="")
        except Exception:
            logger.exception("Communication spec failed for %s; using a fallback sketch", name)
            protocol = str(svc.get("proposed_api_type") or svc.get("api_type") or "REST")
            updated = dict(svc)
            updated["proposed_api_type"] = protocol
            updated["api_type"] = protocol
            updated["api_design"] = updated.get("api_design") or (
                f"## {name} communication spec ({protocol})\n\n"
                "- Honor the architect communication schemes for this service.\n"
                "- Request/response for queries and commands this service owns.\n"
                "- Pub/sub events when notifying peers of state changes.\n"
                "- Do not invent a competing protocol that contradicts the architect package.\n"
            )
            if not comms_are_concrete(updated["api_design"]):
                updated["api_design"] += (
                    "\n### Assumed REST surface\n"
                    f"- `POST /v1/{name.lower()}` create\n"
                    f"- `GET /v1/{name.lower()}/{{id}}` read\n"
                    f"- `PATCH /v1/{name.lower()}/{{id}}` update\n"
                )
            updated["status"] = "awaiting_comms"
            fallback = close_user_message(
                f"Could not parse a communication spec from the model; starting from "
                f"the architect schemes for **{name}**. Complete the spec, then approve.",
                svc=updated,
            )
            msgs = list(updated.get("messages") or [])
            msgs.append(
                {
                    "role": "assistant",
                    "content": fallback,
                    "node": "comms",
                }
            )
            updated["messages"] = msgs
        out = replace_service(out, updated)
    live = [s for s in out if s.get("status") != "suspended"]
    names = ", ".join((s.get("names") or ["service"])[-1] for s in live) or "none"
    notice = close_user_message(
        f"Opened planning tiles for {len(live)} microservice(s) at once: {names}. "
        "Complete each service's communication spec from the architect protocols, then "
        "interview features, then stack. Each tile can hand off independently.",
        mode="idle",
        can_approve=False,
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
