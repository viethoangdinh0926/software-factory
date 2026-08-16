from __future__ import annotations

from typing import Any

from orchestrator_agent.graph.nodes.common import approve_label, invoke_json, skill_digest


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    pending = (state.get("pending_user_feedback") or "").strip()
    package = state.get("package_markdown") or ""
    track = state.get("architect_track") or "unset"
    result = invoke_json(
        system=(
            "You are the orchestrator topology classifier.\n"
            f"{skill_digest()}\n\n"
            "Classify whether the architect design package describes a stand-alone "
            "application or a distributed system of microservices.\n"
            "Bias: architect track `lld` → standalone; `hld` → distributed, unless the "
            "package clearly contradicts that (e.g. HLD modular monolith).\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "topology": "standalone" | "distributed",\n'
            '  "certain": boolean,\n'
            '  "rationale": string,\n'
            '  "assistant_message": string\n'
            "}\n"
        ),
        user=(
            f"Architect track: `{track}`\n\n"
            f"User correction (if any):\n{pending or '(none)'}\n\n"
            f"Design package:\n{package[:12000]}\n"
        ),
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
    msgs = [{"role": "assistant", "content": assistant, "node": "classify"}]
    if certain:
        return {
            "topology": topology,
            "topology_certain": True,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "phase": "classify",
            "route": "handle_update",
            "wait_kind": "",
            "can_approve": False,
            "messages": msgs,
        }
    return {
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
    }
