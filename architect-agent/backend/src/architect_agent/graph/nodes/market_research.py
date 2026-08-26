from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from architect_agent.design_progress import (
    NEW_ROUND_AFTER_HANDOFF,
    max_track_step,
    rewind_or_block_skip,
)
from architect_agent.graph.nodes.common import answer_before_approve, approve_label, gate_user_chat
from architect_agent.context_budget import refresh_discussion_digest
from architect_agent.graph.state import DesignGraphState
from architect_agent.market_research import generate_market_evaluation_report
from architect_agent.query_intent import (
    is_informational_query,
    is_revision_request,
    promote_chat_to_approve,
    resolve_wait_action,
    with_next_prompt,
)


def market_research_node(state: DesignGraphState) -> dict[str, Any]:
    """Re-evaluate the market against the latest design package (every design approve)."""
    business_spec = state.get("business_spec") or ""
    diagram = state.get("design_diagram") or ""
    justification = state.get("design_justification") or ""
    ledger = state.get("tradeoff_ledger") or ""
    scale = state.get("scale_estimates") or ""
    prior_grade = state.get("market_evaluation_grade") or ""
    prior_report = state.get("market_evaluation_report") or ""

    research_blob = (
        f"{business_spec}\n\n"
        f"## Discussion memory\n{state.get('discussion_digest') or '(none)'}\n\n"
        f"## Scale estimates\n{scale}\n\n"
        f"## Trade-off ledger\n{ledger}\n\n"
        f"## Current design diagram\n```mermaid\n{diagram}\n```\n\n"
        f"## Design justification\n{justification}\n"
    )
    if prior_grade or prior_report:
        research_blob += (
            f"\n## Prior market grade\n{prior_grade}\n\n"
            f"## Prior market report (for delta context)\n{prior_report[:4000]}\n"
        )

    result = generate_market_evaluation_report(research_blob)
    report = result["report_markdown"]
    grade = result["grade"]
    summary = result["summary"]
    track = state.get("design_track") or "hld"
    version_hint = "this design version"

    assistant_message = (
        f"Market evaluation for {version_hint} complete — idea grade **{grade}**.\n\n"
        f"{summary}\n\n"
        "Review the **Market evaluation** report. If you have no other concerns, please "
        "approve so we can hand off the design package. After that "
        "attempt, a new design round starts at Phase 0."
    )
    digest = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending="",
        assistant=assistant_message,
        phase="market_research",
        track=str(track or ""),
        spec=f"Market grade {grade}. {summary[:400]}",
    )

    return {
        "phase": "market_research",
        "market_evaluation_report": report,
        "market_evaluation_grade": grade,
        "market_evaluation_done": True,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant_message,
        "publish_requested": False,
        "resume_after_market": True,
        "stay_on_interrupt": False,
        "discussion_digest": digest,
        "messages": [
            {"role": "assistant", "content": assistant_message, "node": "market_research"}
        ],
    }


def market_wait_node(state: DesignGraphState) -> dict[str, Any]:
    """Pause for market report review; continue triggers handoff + design resume."""
    report = state.get("market_evaluation_report") or ""
    grade = state.get("market_evaluation_grade") or ""
    track = state.get("design_track") or "hld"
    label = approve_label("market_research", track, 0)
    assistant_message = with_next_prompt(
        state.get("pending_assistant_message") or "",
        approve_label=label,
        can_approve=True,
    )

    resume = interrupt(
        {
            "phase": "market_research",
            "design_track": track,
            "design_step": int(state.get("design_step") or 0),
            "assistant_message": assistant_message,
            "business_spec": state.get("business_spec") or "",
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "design_diagram": state.get("design_diagram") or "",
            "design_justification": state.get("design_justification") or "",
            "market_evaluation_report": report,
            "market_evaluation_grade": grade,
            "can_approve": True,
            "can_download_market_report": True,
            "approve_kind": "continue_after_market",
            "approve_label": label,
            "ready_for_design": True,
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    keynotes, kind, clar = gate_user_chat(
        state,
        user_text,
        action=action,
        node="market_research",
        stay={
            "phase": "market_research",
            "design_track": track if track in {"lld", "hld"} else "hld",
            "design_step": int(state.get("design_step") or 0),
            "market_evaluation_done": True,
            "resume_after_market": True,
            "ready_to_advance": True,
        },
    )
    if clar:
        return clar
    state["discussion_digest"] = keynotes
    last_as = str(state.get("pending_assistant_message") or "")
    action = resolve_wait_action(action, user_text, last_as, consult_kind=kind)
    msgs: list[dict[str, Any]] = []
    if user_text and action in {"chat", "revise", "answer"}:
        msgs.append({"role": "user", "content": user_text, "node": "market_research"})

    if action == "session_done":
        msgs.append(
            {"role": "assistant", "content": "Session marked done.", "node": "market_research"}
        )
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if action in {"chat", "revise"} and user_text:
        rewound = rewind_or_block_skip(
            state,
            user_text,
            node="market_research",
            current_phase="market_research",
            current_track=track if track in {"lld", "hld"} else "hld",
            current_step=int(state.get("design_step") or 0),
            msgs=msgs,
        )
        if rewound is not None:
            return rewound

    if action in {"answer", "revise"} and user_text:
        return answer_before_approve(
            state,
            user_text,
            node="market_research",
            base={
                "phase": "market_research",
                "design_track": track if track in {"lld", "hld"} else "hld",
                "design_step": int(state.get("design_step") or 0),
                "market_evaluation_done": True,
                "market_evaluation_report": report,
                "market_evaluation_grade": grade,
                "resume_after_market": True,
                "ready_to_advance": True,
            },
        )

    proceed_msg = "Closing market evaluation and returning to Phase 0."
    msgs.append({"role": "assistant", "content": proceed_msg, "node": "market_research"})

    track_name = track if track in {"lld", "hld"} else "hld"
    digest = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending=user_text,
        assistant=proceed_msg,
        phase="market_research",
        track=str(track_name),
        spec=str(state.get("business_spec") or ""),
    )
    return {
        "phase": "phase0",
        "design_track": track_name,
        "design_step": 0,
        "market_evaluation_done": True,
        "market_evaluation_report": report,
        "market_evaluation_grade": grade,
        "publish_requested": True,
        "resume_after_market": False,
        "ready_to_advance": True,
        "design_ready_to_approve": False,
        "interview_complete": True,
        "spec_compiled": True,
        "pending_user_feedback": "",
        "pending_assistant_message": NEW_ROUND_AFTER_HANDOFF,
        "carry_change": "",
        "rewalk_until_step": max_track_step(track_name),
        "stay_on_interrupt": True,
        "discussion_digest": digest,
        "messages": msgs,
    }
