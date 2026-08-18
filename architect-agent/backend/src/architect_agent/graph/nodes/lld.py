from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_history_tail,
    maybe_compact_business_spec,
    maybe_compact_design_justification,
)
from architect_agent.graph.nodes.common import (
    answer_before_approve,
    approve_label,
    invoke_json,
    is_design_approve_step,
)
from architect_agent.graph.state import DesignGraphState
from architect_agent.json_util import coerce_diagram_text
from architect_agent.mermaid_sanitize import sanitize_mermaid
from architect_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    is_informational_query,
    promote_chat_to_approve,
    with_resolution_close,
)

_STEP_TITLES = {
    1: "Information gathering",
    2: "Architectural blueprint",
    3: "Verification",
}


def lld_step_node(state: DesignGraphState) -> dict[str, Any]:
    """Run the current LLD step (1 gather / 2 blueprint / 3 verify)."""
    step = max(1, min(3, int(state.get("design_step") or 1)))
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "lld"]
    history_tail = format_history_tail(prior)
    ledger = state.get("tradeoff_ledger") or ""
    diagram = state.get("design_diagram") or ""
    justification = maybe_compact_design_justification(state.get("design_justification") or "")

    result = invoke_json(
        system=(
            "You are the Architect agent's LLD track node (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            f"Current LLD step: {step} — {_STEP_TITLES.get(step, '')}.\n"
            "Fill THIS step's primary artifact in full using recommended defaults. "
            "Do not stall on missing details.\n"
            "Step 1 primary: updated_business_spec — business rules, concurrency, "
            "lifecycle, invariants. ready_to_advance=true when spec is structured.\n"
            "Step 2 primary: design_diagram_lines (class/structure Mermaid, ≥8 nodes) "
            "+ design_justification (patterns, SOLID). ready_to_advance=true when both exist.\n"
            "Step 3 primary: design_justification verification notes; "
            "design_ready_to_approve=true when blueprint is coherent. Invite Approve & send.\n"
            "Leave non-primary fields as \"\" / [] to preserve prior values, EXCEPT you "
            "must always fill the primary field(s) for this step (never empty).\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "updated_business_spec": string,\n'
            '  "tradeoff_ledger": string,\n'
            '  "design_diagram_lines": [string, ...],\n'
            '  "design_diagram": string,\n'
            '  "design_justification": string,\n'
            '  "ready_to_advance": boolean,\n'
            '  "design_ready_to_approve": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
            "Escape newlines as \\n. Prefer design_diagram_lines for Mermaid.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
        ),
        user=(
            f"Living specification:\n\n{business_spec}\n\n"
            f"Trade-off ledger:\n{ledger or '(empty)'}\n\n"
            f"Current diagram:\n{diagram or '(none)'}\n\n"
            f"Current justification:\n{justification or '(none)'}\n\n"
            f"Recent LLD turns:\n{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none)'}\n"
        ),
    )

    new_diagram = sanitize_mermaid(coerce_diagram_text(result, fallback=diagram))
    new_just = maybe_compact_design_justification(
        str(result.get("design_justification") or justification)
    )
    ready_advance = bool(result.get("ready_to_advance"))
    design_ready = bool(result.get("design_ready_to_approve")) or (
        step >= 3 and bool(new_diagram.strip())
    )
    if step >= 3:
        ready_advance = design_ready
    assistant = result.get("assistant_message") or f"LLD step {step} update."
    if pending:
        changed = (
            str(result.get("updated_business_spec") or business_spec) != business_spec
            or str(result.get("tradeoff_ledger") or ledger) != ledger
            or new_diagram != diagram
            or new_just != justification
        )
        assistant = with_resolution_close(str(assistant), changed=changed)

    return {
        "phase": "lld",
        "design_track": "lld",
        "design_step": step,
        "business_spec": result.get("updated_business_spec") or business_spec,
        "tradeoff_ledger": result.get("tradeoff_ledger") or ledger,
        "design_diagram": new_diagram,
        "design_justification": new_just,
        "ready_to_advance": ready_advance,
        "design_ready_to_approve": design_ready,
        "ready_for_design": design_ready,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "messages": [{"role": "assistant", "content": assistant, "node": "lld"}],
    }


def lld_wait_node(state: DesignGraphState) -> dict[str, Any]:
    step = max(1, min(3, int(state.get("design_step") or 1)))
    ready = bool(state.get("ready_to_advance"))
    design_ready = bool(state.get("design_ready_to_approve"))
    assistant = state.get("pending_assistant_message") or ""
    design_approve = is_design_approve_step("lld", step) and design_ready

    resume = interrupt(
        {
            "phase": "lld",
            "design_track": "lld",
            "design_step": step,
            "assistant_message": assistant,
            "business_spec": state.get("business_spec") or "",
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "design_diagram": state.get("design_diagram") or "",
            "design_justification": state.get("design_justification") or "",
            "ready_to_advance": ready,
            "design_ready_to_approve": design_ready,
            "can_approve": ready or design_approve,
            "approve_kind": "design" if design_approve else "advance",
            "approve_label": approve_label("lld", "lld", step, design_ready=design_ready),
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    action = promote_chat_to_approve(
        action, user_text, can_approve=ready or design_approve
    )
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "lld"})

    if action == "approve" and design_approve:
        msg = (
            "Design version queued for market evaluation, then handoff to the "
            "Orchestrator. Review the market report when it appears."
        )
        msgs.append({"role": "assistant", "content": msg, "node": "lld"})
        return {
            "phase": "market_research",
            "design_track": "lld",
            "design_step": step,
            "publish_requested": False,
            "resume_after_market": True,
            "market_evaluation_done": False,
            "pending_user_feedback": "",
            "pending_assistant_message": msg,
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if action == "approve" and ready and step < 3:
        next_step = step + 1
        msg = f"Advancing to LLD step {next_step}: {_STEP_TITLES.get(next_step, '')}."
        msgs.append({"role": "assistant", "content": msg, "node": "lld"})
        return {
            "phase": "lld",
            "design_track": "lld",
            "design_step": next_step,
            "ready_to_advance": False,
            "design_ready_to_approve": False,
            "pending_user_feedback": "",
            "pending_assistant_message": msg,
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "lld"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if user_text and is_informational_query(user_text):
        return answer_before_approve(
            state,
            user_text,
            node="lld",
            base={
                "phase": "lld",
                "design_track": "lld",
                "design_step": step,
                "ready_to_advance": ready,
                "design_ready_to_approve": design_ready,
            },
        )

    return {
        "phase": "lld",
        "design_track": "lld",
        "design_step": step,
        "ready_to_advance": False,
        "pending_user_feedback": user_text,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "messages": msgs,
    }
