"""Per-step workflow tiles for engineer sub-agents."""

from __future__ import annotations

import contextvars
from typing import Any

from engineer_agent.ascii_text import fold_to_ascii

_WORKFLOW_POSITION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "engineer_workflow_position", default=""
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
        "- This turn must keep the user on that step unless they approved, paused, or asked to execute.\n"
        "- Fill THIS step's own artifact. Do not start a later step's artifact.\n"
        "- The UI tile and package section for this step are what you write now.\n\n"
        f"{body}"
    )


def _tile(tile_id: str, title: str, status: str, body: str = "") -> dict[str, Any]:
    return {
        "id": tile_id,
        "title": title,
        "status": status,
        "kind": "markdown",
        "body": fold_to_ascii(body or ""),
        "diagram": "",
    }


def sub_current_id(status: str) -> str:
    mapping = {
        "awaiting_plan": "plan",
        "paused": "plan",
        "executing": "execute",
        "blocked": "blocked",
        "shipped": "ship",
        "ready": "ship",
        "suspended": "plan",
    }
    return mapping.get(status, "plan")


def sub_workflow_tiles(sub: dict[str, Any]) -> dict[str, Any]:
    status = str(sub.get("status") or "")
    current_id = sub_current_id(status)
    plan = sub.get("execution_plan") if isinstance(sub.get("execution_plan"), dict) else {}
    summary = str(plan.get("summary") or "")
    items = plan.get("items") if isinstance(plan.get("items"), list) else []
    plan_body = summary
    if items:
        lines = [summary, ""] if summary else []
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- P{item.get('priority', '')} [{item.get('kind', '')}] "
                f"{item.get('title', '')} ({item.get('status', '')})"
            )
        plan_body = "\n".join(lines).strip()
    notes = str(sub.get("implementation_notes") or "")
    offered = str(sub.get("offered_api") or "")
    issue = sub.get("block_issue") if isinstance(sub.get("block_issue"), dict) else {}
    issue_body = ""
    if issue:
        issue_body = "\n".join(
            part
            for part in (
                str(issue.get("title") or ""),
                str(issue.get("detail") or ""),
                str(issue.get("instructions") or ""),
            )
            if part.strip()
        )
    titles = {
        "plan": "Execution plan",
        "api": "Offered API",
        "execute": "Implementation notes",
        "blocked": "Blocked issue",
        "ship": "Ship",
    }
    tiles = [
        _tile("plan", titles["plan"], "current" if current_id == "plan" else ("done" if plan_body else "pending"), plan_body),
        _tile("api", titles["api"], "done" if offered.strip() else "pending", offered),
        _tile(
            "execute",
            titles["execute"],
            "current" if current_id == "execute" else ("done" if notes.strip() else "pending"),
            notes,
        ),
    ]
    if current_id == "blocked" or issue_body:
        tiles.append(
            _tile("blocked", titles["blocked"], "current" if current_id == "blocked" else "done", issue_body)
        )
    if current_id == "ship" or status in {"shipped", "ready"}:
        tiles.append(
            _tile(
                "ship",
                titles["ship"],
                "current" if current_id == "ship" else "done",
                str(sub.get("git_ship_status") or status),
            )
        )
    return {
        "id": current_id,
        "phase": status,
        "title": titles.get(current_id, status),
        "tiles": tiles,
    }


def sub_package_markdown(sub: dict[str, Any]) -> str:
    wf = sub_workflow_tiles(sub)
    name = str(sub.get("microservice_name") or sub.get("microservice_id") or "service")
    parts = [f"# Engineer package - {name}\n", f"Current step: {wf['title']}\n", "\n---\n"]
    for tile in wf["tiles"]:
        body = str(tile.get("body") or "").strip()
        if not body:
            continue
        parts.append(f"## {tile['title']}\n\n{body}\n\n---\n")
    return fold_to_ascii("".join(parts))


def fleet_package_markdown(session: Any) -> str:
    parts = [
        "# Engineer fleet package\n",
        f"Design session: `{getattr(session, 'design_session_id', '')}`\n",
        f"Design version: `{getattr(session, 'design_version', 0)}`\n",
        "\n---\n",
    ]
    for sub in getattr(session, "sub_agents", []) or []:
        if str((sub or {}).get("status") or "") == "suspended":
            continue
        parts.append(sub_package_markdown(sub))
        parts.append("\n")
    return fold_to_ascii("".join(parts))
