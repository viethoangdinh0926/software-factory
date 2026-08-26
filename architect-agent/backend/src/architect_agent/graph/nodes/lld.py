from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_phase_context,
    maybe_compact_business_spec,
    maybe_compact_design_justification,
    refresh_discussion_digest,
)
from architect_agent.graph.nodes.common import (
    LLD_STEP_TITLES,
    answer_before_approve,
    approve_label,
    ensure_step_briefing,
    gate_user_chat,
    invoke_json,
    is_design_approve_step,
)
from architect_agent.design_progress import (
    KEEP_AND_PATCH_RULES,
    keep_or_patch,
    rewind_or_block_skip,
    should_keep_and_patch,
    with_rewind_notice,
)
from architect_agent.design_diagram import (
    catalog_covers_diagram,
    diagram_is_concrete,
    ensure_component_catalog,
    ensure_design_diagram,
    extract_spec_section,
    upsert_spec_section,
    with_component_walkthrough,
)
from architect_agent.graph.state import DesignGraphState
from architect_agent.json_util import coerce_diagram_text
from architect_agent.mermaid_sanitize import sanitize_mermaid
from architect_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    user_message_first_block,
    is_advance_request,
    is_informational_query,
    promote_chat_to_approve,
    resolve_wait_action,
    with_next_prompt,
    with_resolution_close,
)
from architect_agent.workflow import workflow_prompt_block

_STEP_TITLES = LLD_STEP_TITLES


def lld_step_node(state: DesignGraphState) -> dict[str, Any]:
    """Run the current LLD step (1 gather / 2 blueprint / 3 verify)."""
    step = max(1, min(3, int(state.get("design_step") or 1)))
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "lld"]
    history_tail = format_phase_context(
        str(state.get("discussion_digest") or ""), prior, "lld"
    )
    ledger = state.get("tradeoff_ledger") or ""
    diagram = state.get("design_diagram") or ""
    justification = maybe_compact_design_justification(state.get("design_justification") or "")
    keep_mode = should_keep_and_patch(state, step)
    carry = str(state.get("carry_change") or "").strip()
    keep_block = KEEP_AND_PATCH_RULES if keep_mode else ""

    result = invoke_json(
        system=(
            "You are the Architect agent's LLD track node (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            f"{workflow_prompt_block('lld', 'lld', step)}"
            f"Current LLD step: {step} — {_STEP_TITLES.get(step, '')}.\n"
            f"{user_message_first_block(pending)}"
            f"{keep_block}"
            "Fill THIS step's primary artifact in full using recommended defaults. "
            "If the user just commented, address that comment before restating the step.\n"
            "Do not stall on missing details.\n"
            "Step 1 primary: updated_business_spec with a ## In-process rules section "
            "covering business rules, concurrency, lifecycle, and invariants. Keep other "
            "spec sections. ready_to_advance=true when that section is structured.\n"
            "Step 2 primary: design_diagram_lines (class/structure Mermaid, ≥8 nodes) "
            "+ design_justification (patterns, SOLID). ready_to_advance=true when both exist.\n"
            "Step 3 primary: design_justification verification notes; "
            "design_ready_to_approve=true when blueprint is coherent. Ask them to confirm, "
            "approve, or agree before sending. Never tell them to click a button.\n"
            "Leave non-primary fields as \"\" / [] to preserve prior values, EXCEPT you "
            "must always fill the primary field(s) for this step (never empty) unless "
            "a keep-existing-artifacts instruction is in force.\n"
            "assistant_message MUST brief what this step completed: name the rules, "
            "types, or diagram nodes you wrote, why those defaults, what you rejected, "
            "and what it costs. On step 2, walk through EVERY diagram component by name "
            "and say what it owns. Never write a status line such as "
            "\"LLD step 1 update.\" or \"LLD step 2 update.\"\n"
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
            f"{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none)'}\n"
            f"Carry-forward change to apply if this step is affected:\n{carry or '(none)'}\n"
        ),
    )

    new_diagram = sanitize_mermaid(
        coerce_diagram_text(result, fallback=diagram)
    )
    new_diagram = sanitize_mermaid(keep_or_patch(new_diagram, diagram))
    if step >= 2 and not diagram_is_concrete(new_diagram):
        new_diagram = ensure_design_diagram(
            business_spec,
            new_diagram or diagram,
            track="lld",
            allow_llm=True,
        )
    if keep_mode:
        new_just = maybe_compact_design_justification(
            keep_or_patch(str(result.get("design_justification") or ""), justification)
        )
        new_spec = keep_or_patch(str(result.get("updated_business_spec") or ""), business_spec)
        new_ledger = keep_or_patch(str(result.get("tradeoff_ledger") or ""), ledger)
    else:
        new_just = maybe_compact_design_justification(
            str(result.get("design_justification") or justification)
        )
        new_spec = str(result.get("updated_business_spec") or business_spec)
        new_ledger = str(result.get("tradeoff_ledger") or ledger)
    if step == 1 and not extract_spec_section(new_spec, "In-process rules"):
        incoming = str(result.get("updated_business_spec") or "").strip()
        if incoming and not re.search(r"(?im)^##\s+(problem|actors|goals)", incoming):
            new_spec = upsert_spec_section(business_spec, "In-process rules", incoming)
        elif incoming:
            new_spec = upsert_spec_section(new_spec, "In-process rules", incoming)
    ready_advance = bool(result.get("ready_to_advance"))
    if step == 2:
        ready_advance = diagram_is_concrete(new_diagram)
    design_ready = bool(result.get("design_ready_to_approve")) or (
        step >= 3 and diagram_is_concrete(new_diagram)
    )
    if step >= 3:
        ready_advance = design_ready
    if keep_mode:
        ready_advance = True
        if step >= 3:
            design_ready = True
            ready_advance = True
    primary_field = {1: "business_spec", 2: "design_diagram", 3: "design_justification"}.get(
        step, "business_spec"
    )
    catalog = ""
    if step >= 2 and diagram_is_concrete(new_diagram):
        catalog = ensure_component_catalog(
            new_diagram,
            new_spec,
            new_just,
            allow_llm=True,
        )
        if not catalog_covers_diagram(new_just, new_diagram):
            new_just = catalog
        new_spec = upsert_spec_section(new_spec, "Diagram components", catalog)
    assistant = ensure_step_briefing(
        str(result.get("assistant_message") or ""),
        track="lld",
        step=step,
        title=_STEP_TITLES.get(step, "Low-level design"),
        artifacts={
            "business_spec": new_spec,
            "tradeoff_ledger": new_ledger,
            "design_diagram": new_diagram,
            "design_justification": catalog or new_just,
        },
        primary_field="design_justification" if step == 2 and catalog else primary_field,
        pending=pending,
    )
    if catalog and step == 2:
        assistant = with_component_walkthrough(assistant, catalog)
    if pending:
        changed = (
            new_spec != business_spec
            or new_ledger != ledger
            or new_diagram != diagram
            or new_just != justification
        )
        assistant = with_resolution_close(str(assistant), changed=changed)
    assistant = with_rewind_notice(str(assistant), str(state.get("rewind_notice") or ""))
    digest = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending=pending,
        assistant=assistant,
        phase="lld",
        track="lld",
        spec=new_spec,
    )

    return {
        "phase": "lld",
        "design_track": "lld",
        "design_step": step,
        "business_spec": new_spec,
        "tradeoff_ledger": new_ledger,
        "design_diagram": new_diagram,
        "design_justification": new_just,
        "ready_to_advance": ready_advance,
        "design_ready_to_approve": design_ready,
        "ready_for_design": design_ready,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "rewind_notice": "",
        "publish_requested": False,
        "stay_on_interrupt": False,
        "discussion_digest": digest,
        "messages": [{"role": "assistant", "content": assistant, "node": "lld"}],
    }


def lld_wait_node(state: DesignGraphState) -> dict[str, Any]:
    step = max(1, min(3, int(state.get("design_step") or 1)))
    ready = bool(state.get("ready_to_advance"))
    design_ready = bool(state.get("design_ready_to_approve"))
    design_approve = is_design_approve_step("lld", step) and design_ready
    assistant = with_next_prompt(
        state.get("pending_assistant_message") or "",
        approve_label=approve_label("lld", "lld", step, design_ready=design_ready),
        can_approve=ready or design_approve,
    )

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
    keynotes, kind, clar = gate_user_chat(
        state,
        user_text,
        action=action,
        node="lld",
        stay={
            "phase": "lld",
            "design_track": "lld",
            "design_step": step,
            "ready_to_advance": ready,
            "design_ready_to_approve": design_ready,
        },
    )
    if clar:
        return clar
    state["discussion_digest"] = keynotes
    last_as = str(state.get("pending_assistant_message") or "")
    action = resolve_wait_action(action, user_text, last_as, consult_kind=kind)
    advance_now = is_advance_request(user_text, last_as)
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "lld"})

    if action == "approve" and is_design_approve_step("lld", step):
        msg = (
            "Wrapping up this step and moving on. "
            "Design version queued for market evaluation, then handoff to the "
            "Orchestrator. Review the market report when it appears."
            if advance_now
            else (
                "Design version queued for market evaluation, then handoff to the "
                "Orchestrator. Review the market report when it appears."
            )
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
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if action == "approve" and step < 3:
        next_step = step + 1
        title = _STEP_TITLES.get(next_step, "")
        msg = (
            f"Wrapping up this step and moving on. Next: LLD step {next_step} — {title}."
            if advance_now
            else f"Advancing to LLD step {next_step}: {title}."
        )
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
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "lld"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if user_text:
        rewound = rewind_or_block_skip(
            state,
            user_text,
            node="lld",
            current_phase="lld",
            current_track="lld",
            current_step=step,
            msgs=msgs,
        )
        if rewound is not None:
            return rewound

    if action == "answer" and user_text:
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
        "discussion_digest": keynotes,
        "messages": msgs,
    }
