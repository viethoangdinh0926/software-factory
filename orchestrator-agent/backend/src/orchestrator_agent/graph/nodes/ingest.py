from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import empty_service
from orchestrator_agent.package_parse import parse_design_package


def ingest_node(state: dict[str, Any]) -> dict[str, Any]:
    raw = (state.get("pending_package") or state.get("package_markdown") or "").strip()
    parsed = parse_design_package(raw)
    is_update = bool(state.get("has_ingested"))
    previous_services = list(state.get("services") or []) if is_update else []
    previous_track = str(state.get("architect_track") or "") if is_update else ""
    previous_topology = str(state.get("topology") or "unset") if is_update else "unset"
    previous_app_status = str(state.get("app_status") or "") if is_update else ""
    session_id = parsed.design_session_id or str(state.get("design_session_id") or "")
    notice = (
        f"Received design package v{parsed.design_version} "
        f"(track `{parsed.architect_track or 'unset'}`)."
    )
    msgs = [{"role": "system", "content": notice, "node": "ingest"}]
    return {
        "design_session_id": session_id,
        "package_markdown": parsed.markdown or raw,
        "design_diagram": parsed.design_diagram or state.get("design_diagram") or "",
        "design_version": parsed.design_version,
        "architect_track": parsed.architect_track or "unset",
        "pending_package": "",
        "ingest_kind": "update" if is_update else "first",
        "has_ingested": True,
        "previous_track": previous_track,
        "previous_topology": previous_topology,
        "previous_services": previous_services,
        "previous_app_status": previous_app_status,
        "pending_engineer_actions": [],
        "phase": "classify",
        "route": "classify",
        "wait_kind": "",
        "messages": msgs,
        "finalized": False,
    }


def _suspend_actions(
    *,
    design_session_id: str,
    design_version: int,
    reason: str,
    previous_topology: str,
    previous_services: list[dict[str, Any]],
    previous_app_status: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    suspended: list[dict[str, Any]] = []
    if previous_topology == "standalone" and previous_app_status not in {"", "suspended"}:
        actions.append(
            {
                "action": "suspend",
                "design_session_id": design_session_id,
                "design_version": design_version,
                "microservice_id": None,
                "reason": reason,
            }
        )
    for svc in previous_services:
        if svc.get("status") == "suspended":
            suspended.append(svc)
            continue
        sid = str(svc.get("microservice_id") or "")
        if not sid:
            continue
        actions.append(
            {
                "action": "suspend",
                "design_session_id": design_session_id,
                "design_version": design_version,
                "microservice_id": sid,
                "reason": reason,
            }
        )
        marked = dict(svc)
        marked["status"] = "suspended"
        suspended.append(marked)
    return actions, suspended


def handle_update_node(state: dict[str, Any]) -> dict[str, Any]:
    topology = str(state.get("topology") or "unset")
    ingest_kind = str(state.get("ingest_kind") or "first")
    session_id = str(state.get("design_session_id") or "")
    version = int(state.get("design_version") or 1)
    previous_track = str(state.get("previous_track") or "")
    previous_topology = str(state.get("previous_topology") or "unset")
    new_track = str(state.get("architect_track") or "")
    msgs: list[dict[str, Any]] = []

    if ingest_kind != "update":
        route = "feature_discuss" if topology == "standalone" else "extract_services"
        return {
            "phase": "features" if topology == "standalone" else "extract",
            "route": route,
            "app_status": "planning" if topology == "standalone" else "",
            "pending_engineer_actions": [],
        }

    track_changed = (
        previous_track in {"lld", "hld"}
        and new_track in {"lld", "hld"}
        and previous_track != new_track
    )
    topology_changed = (
        previous_topology in {"standalone", "distributed"}
        and topology in {"standalone", "distributed"}
        and previous_topology != topology
    )

    if track_changed or topology_changed:
        reason = "design_track_changed" if track_changed else "topology_changed"
        actions, _suspended = _suspend_actions(
            design_session_id=session_id,
            design_version=version,
            reason=reason,
            previous_topology=previous_topology,
            previous_services=list(state.get("previous_services") or []),
            previous_app_status=str(state.get("previous_app_status") or ""),
        )
        msgs.append(
            {
                "role": "system",
                "content": (
                    f"Design type changed ({previous_track or previous_topology} → "
                    f"{new_track or topology}). Suspended prior development units and "
                    "starting the new package as a first version."
                ),
                "node": "ingest",
            }
        )
        route = "feature_discuss" if topology == "standalone" else "extract_services"
        return {
            "ingest_kind": "first",
            "services": [],
            "active_service_id": "",
            "tech_stack": "",
            "feature_spec": "",
            "plan_spec": "",
            "api_type": "",
            "api_design": "",
            "entity_relationships": "",
            "app_status": "planning" if topology == "standalone" else "",
            "previous_services": [],
            "pending_engineer_actions": actions,
            "phase": "features" if topology == "standalone" else "extract",
            "route": route,
            "messages": msgs,
        }

    if topology == "standalone":
        msgs.append(
            {
                "role": "system",
                "content": (
                    f"Updated stand-alone design v{version}. Re-discussing features, then "
                    "the tech stack. The engineer will adjust implementation."
                ),
                "node": "ingest",
            }
        )
        return {
            "tech_stack": "",
            "plan_spec": "",
            "app_status": "planning",
            "pending_engineer_actions": [],
            "phase": "features",
            "route": "feature_discuss",
            "messages": msgs,
        }

    # Distributed, same track: extract+match next. Preserve previous_services for matcher.
    msgs.append(
        {
            "role": "system",
            "content": (
                f"Updated distributed design v{version}. Matching microservices by role "
                "(name changes are not removals), then walking every planning phase "
                "again for a full update of each live service."
            ),
            "node": "ingest",
        }
    )
    return {
        "pending_engineer_actions": [],
        "phase": "extract",
        "route": "extract_services",
        "messages": msgs,
    }


def reset_planning_fields(svc: dict[str, Any]) -> dict[str, Any]:
    updated = dict(svc)
    updated["api_type"] = ""
    updated["api_type_recommendation"] = ""
    updated["proposed_api_type"] = ""
    updated["api_design"] = ""
    updated["entity_relationships"] = ""
    updated["tech_stack"] = ""
    updated["plan_spec"] = ""
    updated["spec_changelog"] = ""
    updated["status"] = "planning"
    updated["search_notes"] = ""
    return updated


def new_blank_from_match(match: dict[str, Any], microservice_id: str) -> dict[str, Any]:
    name = str(match.get("name") or "Service")
    role = str(match.get("role_key") or name.lower())
    svc = empty_service(
        microservice_id=microservice_id,
        name=name,
        role_key=role,
        contract=str(match.get("contract_summary") or ""),
    )
    return svc
