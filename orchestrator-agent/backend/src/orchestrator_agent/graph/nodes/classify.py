from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import (
    answer_current_artifacts,
    approve_label,
    close_user_message,
    invoke_json,
    session_phase_context,
    skill_digest,
    with_session_digest,
)
from orchestrator_agent.json_util import recover_classify_from_prose
from orchestrator_agent.query_intent import FEEDBACK_RESOLUTION_RULES, is_revision_request, with_resolution_close


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    pending = (state.get("pending_user_feedback") or "").strip()
    package = state.get("package_markdown") or ""
    track = state.get("architect_track") or "unset"
    topology = str(state.get("topology") or "").lower()
    if (
        pending
        and not is_revision_request(pending)
        and topology in {"standalone", "distributed"}
    ):
        assistant = answer_current_artifacts(
            question=pending,
            artifacts=(
                f"Architect track: {track}\n"
                f"Proposed topology: {topology}\n"
                f"Certain: {bool(state.get('topology_certain'))}\n"
                f"Last assistant note:\n{(state.get('pending_assistant_message') or '')[:1500]}\n"
                f"Design package excerpt:\n{package[:8000]}"
            ),
            digest=str(state.get("discussion_digest") or ""),
        )
        return with_session_digest(
            state,
            {
                "topology": topology,
                "topology_certain": bool(state.get("topology_certain")),
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "phase": "classify",
                "route": "wait",
                "wait_kind": "confirm_topology",
                "can_approve": True,
                "approve_kind": "confirm_topology",
                "approve_label": approve_label("confirm_topology"),
                "messages": [{"role": "assistant", "content": assistant, "node": "classify"}],
            },
            pending=pending,
            assistant=assistant,
            phase="classify",
        )
    result = invoke_json(
        system=(
            "You are the orchestrator topology classifier.\n"
            f"{skill_digest()}\n\n"
            "Classify whether the architect design package describes a stand-alone "
            "application or a distributed system of microservices.\n"
            "Bias: architect track `lld` → standalone; `hld` → distributed, unless the "
            "package clearly contradicts that (e.g. HLD modular monolith).\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "topology": "standalone" | "distributed",\n'
            '  "certain": boolean,\n'
            '  "rationale": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"{session_phase_context(state, 'classify')}\n\n"
            f"Architect track: `{track}`\n\n"
            f"Latest user message:\n{pending or '(none)'}\n\n"
            f"Design package:\n{package[:12000]}\n"
        ),
        recover_prose=recover_classify_from_prose,
    )
    topology = str(result.get("topology") or "distributed").lower()
    if topology not in {"standalone", "distributed"}:
        topology = "distributed" if track == "hld" else "standalone" if track == "lld" else "distributed"
    certain = bool(result.get("certain", True))
    if pending.lower() in {"standalone", "stand-alone", "stand alone"}:
        topology = "standalone"
        certain = True
    if pending.lower() in {"distributed", "microservices", "microservice"}:
        topology = "distributed"
        certain = True
    assistant = str(result.get("assistant_message") or "").strip() or (
        f"Classified topology as **{topology}**."
    )
    if pending:
        assistant = with_resolution_close(
            assistant,
            changed=topology != str(state.get("topology") or ""),
        )
    assistant = close_user_message(
        assistant,
        approve_kind="confirm_topology" if not certain else "",
        can_approve=not certain,
        mode="step" if not certain else "idle",
    )
    msgs = [{"role": "assistant", "content": assistant, "node": "classify"}]
    if certain:
        return with_session_digest(
            state,
            {
                "topology": topology,
                "topology_certain": True,
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "phase": "classify",
                "route": "handle_update",
                "wait_kind": "",
                "can_approve": False,
                "messages": msgs,
            },
            pending=pending,
            assistant=assistant,
            phase="classify",
        )
    return with_session_digest(
        state,
        {
            "topology": topology,
            "topology_certain": False,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "phase": "classify",
            "route": "wait",
            "wait_kind": "confirm_topology",
            "can_approve": True,
            "approve_kind": "confirm_topology",
            "approve_label": approve_label("confirm_topology"),
            "messages": msgs,
        },
        pending=pending,
        assistant=assistant,
        phase="classify",
    )
