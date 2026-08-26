"""Per-step workflow tiles for orchestrator planning."""

from __future__ import annotations

import contextvars
from typing import Any

from orchestrator_agent.ascii_text import fold_to_ascii

_WORKFLOW_POSITION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "orchestrator_workflow_position", default=""
)


def set_workflow_position(label: str) -> None:
    _WORKFLOW_POSITION.set((label or "").strip())


def apply_workflow_instruction(system: str) -> str:
    body = system or ""
    if "WORKFLOW POSITION (non-negotiable)" in body:
        return body
    label = _WORKFLOW_POSITION.get()
    if not label:
        return body
    return (
        "WORKFLOW POSITION (non-negotiable):\n"
        f"- You are at: {label}.\n"
        "- This turn must keep the user on that step unless they approved advancing.\n"
        "- Fill THIS step's own artifact. Do not start a later step's artifact.\n"
        "- The UI tile and package section for this step are what you write now.\n\n"
        f"{body}"
    )


def _tile(
    tile_id: str,
    title: str,
    status: str,
    body: str = "",
    *,
    kind: str = "markdown",
) -> dict[str, Any]:
    return {
        "id": tile_id,
        "title": title,
        "status": status,
        "kind": kind,
        "body": fold_to_ascii(body or ""),
        "diagram": "",
    }


def _status(tile_id: str, current_id: str, reached: bool) -> str:
    if tile_id == current_id:
        return "current"
    if reached:
        return "done"
    return "pending"


_SERVICE_STEPS = (
    ("relations", "Entity relationships", ("awaiting_relations", "awaiting_comms", "awaiting_api_type", "awaiting_api_design")),
    ("features", "Features", ("awaiting_features", "discussing_features")),
    ("stack", "Tech stack", ("awaiting_stack",)),
    ("plan", "Plan spec", ("sent", "approved")),
    ("spec_update", "Spec update", ("awaiting_spec_update", "discussing_spec_update")),
)


def service_current_id(status: str) -> str:
    for tile_id, _title, statuses in _SERVICE_STEPS:
        if status in statuses:
            return tile_id
    if status in {"discussing_relations"}:
        return "relations"
    return "relations"


def service_workflow_tiles(svc: dict[str, Any]) -> dict[str, Any]:
    status = str(svc.get("status") or "")
    current_id = service_current_id(status)
    relations = str(svc.get("entity_relationships") or svc.get("api_design") or "")
    features = str(svc.get("feature_spec") or "")
    stack = str(svc.get("tech_stack") or "")
    plan = str(svc.get("plan_spec") or "")
    bugs = str(svc.get("bug_spec") or "")
    spec_update = "\n\n".join(part for part in (features, bugs) if part.strip())
    bodies = {
        "relations": relations,
        "features": features,
        "stack": stack,
        "plan": plan,
        "spec_update": spec_update if status in {"awaiting_spec_update", "discussing_spec_update", "sent", "approved"} else "",
    }
    order = ["relations", "features", "stack", "plan"]
    if status in {"awaiting_spec_update", "discussing_spec_update"} or bugs.strip():
        order.append("spec_update")
    titles = {row[0]: row[1] for row in _SERVICE_STEPS}
    reached_ids: set[str] = set()
    if relations.strip():
        reached_ids.add("relations")
    if features.strip():
        reached_ids.add("features")
    if stack.strip():
        reached_ids.add("stack")
    if plan.strip() or status in {"sent", "approved"}:
        reached_ids.add("plan")
    if "spec_update" in order:
        reached_ids.add("spec_update") if (features.strip() or bugs.strip()) else None
        if features.strip() or bugs.strip():
            reached_ids.add("spec_update")

    tiles = []
    for tile_id in order:
        tiles.append(
            _tile(
                tile_id,
                titles.get(tile_id, tile_id),
                _status(tile_id, current_id, tile_id in reached_ids),
                bodies.get(tile_id, ""),
            )
        )
    return {
        "id": current_id,
        "phase": status,
        "title": titles.get(current_id, status),
        "tiles": tiles,
    }


def session_current_id(phase: str, wait_kind: str, topology: str) -> str:
    wait = wait_kind or phase or "ingest"
    mapping = {
        "ingest": "package",
        "classify": "topology",
        "confirm_topology": "topology",
        "feature_discuss": "features",
        "approve_features": "features",
        "stack_research": "stack",
        "approve_plan": "plan",
        "emit_plan": "plan",
        "spec_update": "spec_update",
        "approve_spec_update": "spec_update",
        "distributed": "services",
    }
    if topology == "distributed" and wait in {"", "idle", "distributed"}:
        return "services"
    return mapping.get(wait, mapping.get(phase, "package"))


def session_workflow_tiles(session: Any) -> dict[str, Any]:
    phase = str(getattr(session, "phase", "") or "")
    wait_kind = str(getattr(session, "wait_kind", "") or "")
    topology = str(getattr(session, "topology", "") or "")
    current_id = session_current_id(phase, wait_kind, topology)
    package = str(getattr(session, "package_markdown", "") or "")
    diagram = str(getattr(session, "design_diagram", "") or "")
    features = str(getattr(session, "feature_spec", "") or "")
    stack = str(getattr(session, "tech_stack", "") or "")
    plan = str(getattr(session, "plan_spec", "") or "")
    tiles = [
        _tile("package", "Architect package", _status("package", current_id, bool(package.strip())), package),
        _tile(
            "topology",
            "Topology",
            _status("topology", current_id, topology not in {"", "unset"}),
            f"Track: {getattr(session, 'architect_track', '')}\nTopology: {topology}",
        ),
    ]
    if diagram.strip():
        tiles.append(
            {
                "id": "diagram",
                "title": "System diagram",
                "status": "done",
                "kind": "diagram",
                "body": "",
                "diagram": fold_to_ascii(diagram),
            }
        )
    if topology != "distributed":
        tiles.append(
            _tile("features", "Features", _status("features", current_id, bool(features.strip())), features)
        )
        tiles.append(_tile("stack", "Tech stack", _status("stack", current_id, bool(stack.strip())), stack))
        tiles.append(_tile("plan", "Plan spec", _status("plan", current_id, bool(plan.strip())), plan))
    else:
        tiles.append(
            _tile(
                "services",
                "Microservice interviews",
                _status("services", current_id, bool(getattr(session, "services", None))),
                f"{len(getattr(session, 'services', []) or [])} core microservices",
            )
        )
    title = next((t["title"] for t in tiles if t["id"] == current_id), phase or wait_kind)
    return {
        "id": current_id,
        "phase": phase,
        "wait_kind": wait_kind,
        "title": title,
        "tiles": tiles,
    }


def plan_package_markdown(session: Any) -> str:
    wf = session_workflow_tiles(session)
    parts = [
        "# Orchestrator Plan Package\n",
        f"Design session: `{getattr(session, 'design_session_id', '')}`\n",
        f"Design version: `{getattr(session, 'design_version', 0)}`\n",
        f"Current step: {wf['title']}\n",
        f"Topology: `{getattr(session, 'topology', '')}`\n",
        "\n---\n",
    ]
    for tile in wf["tiles"]:
        body = str(tile.get("body") or "").strip()
        diagram = str(tile.get("diagram") or "").strip()
        if not body and not diagram:
            continue
        parts.append(f"## {tile['title']}\n\n")
        if body:
            parts.append(body + "\n\n")
        if diagram:
            parts.append("```mermaid\n" + diagram + "\n```\n\n")
        parts.append("---\n")
    for svc in getattr(session, "services", []) or []:
        sw = service_workflow_tiles(svc)
        name = (svc.get("names") or ["service"])[-1] if isinstance(svc.get("names"), list) else svc.get("role_key") or "service"
        parts.append(f"# Service: {name}\n\n")
        for tile in sw["tiles"]:
            body = str(tile.get("body") or "").strip()
            if not body:
                continue
            parts.append(f"## {tile['title']}\n\n{body}\n\n---\n")
    return fold_to_ascii("".join(parts))
