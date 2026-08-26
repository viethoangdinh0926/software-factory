"""Per-step workflow position, UI tiles, and package sections for the architect."""

from __future__ import annotations

import contextvars
from typing import Any

from architect_agent.ascii_text import fold_to_ascii
from architect_agent.design_diagram import extract_spec_section, strip_spec_sections
from architect_agent.graph.nodes.common import design_step_title

_WORKFLOW_POSITION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "architect_workflow_position", default=""
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
        "- This turn must keep the user on that step unless they approved advancing "
        "or the change belongs on an earlier confirmed step.\n"
        "- Fill THIS step's own artifact. Do not start a later step's artifact.\n"
        "- The UI tile and package section for this step are what you write now.\n\n"
        f"{body}"
    )

_SCOPED_HEADINGS = ("In-process rules", "Domain model", "Diagram components")


def workflow_prompt_block(phase: str, track: str, step: int) -> str:
    title = design_step_title(phase, track, step) or phase
    label = current_position_label(phase, track, step)
    return (
        "WORKFLOW POSITION (non-negotiable):\n"
        f"- You are at: {label}.\n"
        f"- This turn must keep the user on that step ({phase} / {track} step {int(step or 0)}"
        f" - {title}) unless they approved advancing or the change belongs on an earlier step.\n"
        "- Fill THIS step's own artifact. Do not start a later step's artifact.\n"
        "- The UI tile and package section for this step are what you write now.\n"
    )


def current_position_label(phase: str, track: str, step: int) -> str:
    title = design_step_title(phase, track, step) or ""
    if phase == "phase0":
        return "Phase 0 - Scope and spec"
    if phase == "market_research":
        return "Market evaluation"
    if phase == "done":
        return "Handoff complete"
    track_u = (track or "").upper()
    if track_u in {"LLD", "HLD"} and int(step or 0) > 0:
        return f"{track_u} step {int(step)} - {title}".strip(" -")
    return title or phase or "design"


def current_tile_id(phase: str, track: str, step: int) -> str:
    if phase == "phase0":
        return "phase0"
    if phase == "market_research":
        return "market"
    if phase == "done":
        return "handoff"
    if track == "lld" and int(step or 0) > 0:
        return f"lld{int(step)}"
    if track == "hld" and int(step or 0) > 0:
        return f"hld{int(step)}"
    return "phase0"


def _status(tile_id: str, current_id: str, reached: bool) -> str:
    if tile_id == current_id:
        return "current"
    if reached:
        return "done"
    return "pending"


def _tile(
    *,
    tile_id: str,
    title: str,
    status: str,
    body: str = "",
    diagram: str = "",
) -> dict[str, Any]:
    kind = "diagram" if diagram.strip() and not (body or "").strip() else "mixed" if diagram.strip() else "markdown"
    return {
        "id": tile_id,
        "title": title,
        "status": status,
        "kind": kind,
        "body": fold_to_ascii(body or ""),
        "diagram": fold_to_ascii(diagram or ""),
    }


def architect_workflow(session: Any) -> dict[str, Any]:
    phase = str(getattr(session, "phase", "") or "")
    track = str(getattr(session, "design_track", "") or "")
    step = int(getattr(session, "design_step", 0) or 0)
    spec = str(getattr(session, "business_spec", "") or "")
    current_id = current_tile_id(phase, track, step)
    tiles: list[dict[str, Any]] = []

    scope_body = strip_spec_sections(spec, _SCOPED_HEADINGS)
    phase0_reached = True
    tiles.append(
        _tile(
            tile_id="phase0",
            title="Phase 0 - Scope and spec",
            status=_status("phase0", current_id, phase0_reached),
            body=scope_body,
        )
    )

    prior_round = phase == "phase0" and int(getattr(session, "design_version", 0) or 0) > 0

    if track == "lld":
        def lld_passed(n: int) -> bool:
            if prior_round or phase in {"market_research", "done"}:
                return True
            return phase == "lld" and step >= n

        rules = extract_spec_section(spec, "In-process rules")
        diagram = str(getattr(session, "design_diagram", "") or "")
        justification = str(getattr(session, "design_justification", "") or "")
        tiles.append(
            _tile(
                tile_id="lld1",
                title="LLD 1 - Information gathering",
                status=_status("lld1", current_id, lld_passed(1) or bool(rules)),
                body=rules,
            )
        )
        tiles.append(
            _tile(
                tile_id="lld2",
                title="LLD 2 - Architectural blueprint",
                status=_status("lld2", current_id, lld_passed(2) or bool(diagram)),
                diagram=diagram if lld_passed(2) or diagram else "",
            )
        )
        tiles.append(
            _tile(
                tile_id="lld3",
                title="LLD 3 - Verification",
                status=_status("lld3", current_id, lld_passed(3) or bool(justification)),
                body=justification,
            )
        )

    if track == "hld":
        def hld_passed(n: int) -> bool:
            if prior_round or phase in {"market_research", "done"}:
                return True
            return phase == "hld" and step >= n

        scale = str(getattr(session, "scale_estimates", "") or "")
        domain = extract_spec_section(spec, "Domain model")
        apis = str(getattr(session, "api_contracts", "") or "")
        comms = str(getattr(session, "communication_schemes", "") or "")
        diagram = str(getattr(session, "design_diagram", "") or "")
        fmea = str(getattr(session, "fmea_notes", "") or "")
        synthesis = str(getattr(session, "design_justification", "") or "")
        tiles.append(
            _tile(
                tile_id="hld1",
                title="HLD 1 - Requirements and capacity estimation",
                status=_status("hld1", current_id, hld_passed(1) or bool(scale)),
                body=scale,
            )
        )
        tiles.append(
            _tile(
                tile_id="hld2",
                title="HLD 2 - Domain object modeling",
                status=_status("hld2", current_id, hld_passed(2) or bool(domain)),
                body=domain,
            )
        )
        tiles.append(
            _tile(
                tile_id="hld3",
                title="HLD 3 - Core Microservices",
                status=_status("hld3", current_id, hld_passed(3) or bool(apis)),
                body=apis,
            )
        )
        tiles.append(
            _tile(
                tile_id="hld4",
                title="HLD 4 - Communication Schemes, infrastructure and system diagram",
                status=_status("hld4", current_id, hld_passed(4) or bool(comms or diagram)),
                body=comms,
                diagram=diagram,
            )
        )
        tiles.append(
            _tile(
                tile_id="hld5",
                title="HLD 5 - Vulnerability and edge-case analysis (FMEA)",
                status=_status("hld5", current_id, hld_passed(5) or bool(fmea)),
                body=fmea,
            )
        )
        tiles.append(
            _tile(
                tile_id="hld6",
                title="HLD 6 - Session synthesis and wrap-up",
                status=_status("hld6", current_id, hld_passed(6) or bool(synthesis)),
                body=synthesis,
            )
        )

    market_report = str(getattr(session, "market_evaluation_report", "") or "")
    market_reached = bool(market_report.strip()) or phase in {"market_research", "done"}
    tiles.append(
        _tile(
            tile_id="market",
            title="Market evaluation",
            status=_status("market", current_id, market_reached),
            body=market_report,
        )
    )
    if str(getattr(session, "tradeoff_ledger", "") or "").strip():
        tiles.append(
            _tile(
                tile_id="ledger",
                title="Trade-off ledger",
                status="done",
                body=str(getattr(session, "tradeoff_ledger") or ""),
            )
        )

    title = current_position_label(phase, track, step)
    return {
        "id": current_id,
        "phase": phase,
        "track": track,
        "step": step,
        "title": title,
        "tiles": tiles,
    }


def package_from_workflow(session: Any) -> str:
    """Download / handoff markdown: one section per workflow tile that has output."""
    wf = architect_workflow(session)
    version = max(int(getattr(session, "design_version", 0) or 0), 1)
    parts = [
        "# System Design Package\n",
        f"Design session: `{getattr(session, 'session_id', '')}`\n",
        f"Design version: `{version}`\n",
        f"Track: `{getattr(session, 'design_track', '')}` step `{getattr(session, 'design_step', 0)}`\n",
        f"Current step: {wf['title']}\n",
        f"Generated: {getattr(session, 'updated_at', '')}\n",
        "\n---\n",
    ]
    for tile in wf["tiles"]:
        body = str(tile.get("body") or "").strip()
        diagram = str(tile.get("diagram") or "").strip()
        if not body and not diagram:
            continue
        parts.append(f"## {tile['title']}\n\n")
        if body:
            parts.append(body)
            parts.append("\n\n")
        if diagram:
            parts.append("```mermaid\n")
            parts.append(diagram)
            parts.append("\n```\n\n")
        parts.append("---\n")
    return fold_to_ascii("".join(parts))
