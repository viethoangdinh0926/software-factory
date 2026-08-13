from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from architect_agent.graph.state import DesignGraphState
from architect_agent.market_research import generate_market_evaluation_report


def market_research_node(state: DesignGraphState) -> dict[str, Any]:
    """Compare the approved spec to popular alternatives and produce an evaluation report."""
    if state.get("market_evaluation_done") and state.get("market_evaluation_report"):
        return {
            "phase": "market_research",
            "pending_assistant_message": state.get("pending_assistant_message") or "",
        }

    business_spec = state.get("business_spec") or ""
    result = generate_market_evaluation_report(business_spec)
    report = result["report_markdown"]
    grade = result["grade"]
    summary = result["summary"]

    assistant_message = (
        f"Spec approved. I researched popular alternatives and graded this idea "
        f"**{grade}**.\n\n{summary}\n\n"
        "Review the **Market evaluation** report in the side panel (you can download it). "
        "When you're ready, click **Continue to system design**."
    )

    return {
        "phase": "market_research",
        "market_evaluation_report": report,
        "market_evaluation_grade": grade,
        "market_evaluation_done": True,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant_message,
        "publish_requested": False,
        "messages": [
            {"role": "assistant", "content": assistant_message, "node": "market_research"}
        ],
    }


def market_wait_node(state: DesignGraphState) -> dict[str, Any]:
    """Pause so the user can read the market evaluation before system design."""
    report = state.get("market_evaluation_report") or ""
    grade = state.get("market_evaluation_grade") or ""
    assistant_message = state.get("pending_assistant_message") or ""

    resume = interrupt(
        {
            "phase": "market_research",
            "assistant_message": assistant_message,
            "business_spec": state.get("business_spec") or "",
            "market_evaluation_report": report,
            "market_evaluation_grade": grade,
            "can_approve": True,
            "can_download_market_report": True,
            "ready_for_design": True,
        }
    )

    action = (resume or {}).get("action", "approve")
    user_text = ((resume or {}).get("text") or "").strip()

    proceed_msg = "Continuing to system design with your approved specification."
    msgs: list[dict[str, Any]] = []
    if action == "chat" and user_text:
        msgs.append({"role": "user", "content": user_text, "node": "market_research"})
        proceed_msg = (
            f"Noted before design: {user_text[:240]}"
            + ("…" if len(user_text) > 240 else "")
            + " Continuing to system design."
        )
    msgs.append({"role": "assistant", "content": proceed_msg, "node": "market_research"})

    return {
        "phase": "system_design",
        "spec_approved": True,
        "market_evaluation_done": True,
        "market_evaluation_report": report,
        "market_evaluation_grade": grade,
        "pending_user_feedback": user_text if action == "chat" else "",
        "pending_assistant_message": proceed_msg,
        "messages": msgs,
    }
