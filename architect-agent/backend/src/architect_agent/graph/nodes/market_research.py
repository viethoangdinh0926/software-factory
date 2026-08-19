from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from architect_agent.graph.nodes.common import answer_before_approve, approve_label
from architect_agent.graph.state import DesignGraphState
from architect_agent.market_research import generate_market_evaluation_report
from architect_agent.query_intent import is_informational_query, promote_chat_to_approve, with_next_prompt


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
        "Review the **Market evaluation** report. When you're ready, click "
        "**Continue after market evaluation** to hand off the design package and "
        f"resume **{str(track).upper()}** design iteration."
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

    action = (resume or {}).get("action", "approve")
    user_text = ((resume or {}).get("text") or "").strip()
    action = promote_chat_to_approve(action, user_text, can_approve=True)
    msgs: list[dict[str, Any]] = []
    if action == "chat" and user_text:
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

    if action == "chat" and user_text and is_informational_query(user_text):
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

    # Resume design iteration: HLD → step 4; LLD → step 3 (verify).
    if track == "lld":
        next_phase = "lld"
        next_step = 3
    else:
        next_phase = "hld"
        next_step = 4

    proceed_msg = (
        "Continuing after market evaluation: design package will be handed off, "
        f"then resume **{str(track).upper()}** at step {next_step}."
    )
    if user_text:
        proceed_msg = (
            f"Noted: {user_text[:240]}"
            + ("…" if len(user_text) > 240 else "")
            + " "
            + proceed_msg
        )
    msgs.append({"role": "assistant", "content": proceed_msg, "node": "market_research"})

    return {
        "phase": next_phase,  # type: ignore[typeddict-item]
        "design_track": track if track in {"lld", "hld"} else "hld",  # type: ignore[typeddict-item]
        "design_step": next_step,
        "market_evaluation_done": True,
        "market_evaluation_report": report,
        "market_evaluation_grade": grade,
        "publish_requested": True,
        "resume_after_market": False,
        "ready_to_advance": False,
        "design_ready_to_approve": False,
        "pending_user_feedback": user_text if action == "chat" else "",
        "pending_assistant_message": proceed_msg,
        "stay_on_interrupt": False,
        "messages": msgs,
    }
