from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from architect_agent.graph.state import DesignGraphState
from architect_agent.llm import get_chat_model

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = _JSON_RE.search(text)
    if not match:
        raise ValueError(f"Expected JSON object in model response: {text[:400]}")
    return json.loads(match.group(0))


def _invoke_json(system: str, user: str) -> dict[str, Any]:
    model = get_chat_model()
    response = model.invoke(
        [SystemMessage(content=system), HumanMessage(content=user)],
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return _parse_json(str(content))


def system_design_node(state: DesignGraphState) -> dict[str, Any]:
    """Propose/revise once per invoke; chat feedback loops back through the graph."""
    business_spec = state.get("business_spec") or ""
    diagram = state.get("design_diagram") or ""
    justification = state.get("design_justification") or ""
    pending_user_feedback = state.get("pending_user_feedback") or ""
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "system_design"]
    history_tail = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in prior[-16:])

    first_draft = not bool(diagram.strip())
    mode = (
        "Create the FIRST DRAFT system design from the approved business specification."
        if first_draft
        else (
            "Revise the CURRENT design using the latest user feedback. "
            "Update design_diagram and design_justification so they reflect the new requirements. "
            "Do not ignore the feedback."
        )
    )

    proposal = _invoke_json(
        system=(
            "You are the Architect agent's system design node.\n"
            "Maintain a living high-level design with a Mermaid diagram and markdown "
            "justification for every component.\n"
            "On every turn after the first draft, treat user chat as design change requests.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "design_diagram": string,\n'
            '  "design_justification": string,\n'
            '  "assistant_message": string,\n'
            '  "style": "monolithic" | "distributed",\n'
            '  "changes_made": string\n'
            "}\n"
            "design_diagram must be raw Mermaid (no markdown fences)."
        ),
        user=(
            f"{mode}\n\n"
            f"Approved business specification:\n\n{business_spec}\n\n"
            f"Current diagram:\n{diagram or '(none — produce first draft)'}\n\n"
            f"Current justification:\n{justification or '(none)'}\n\n"
            f"Design conversation so far:\n{history_tail or '(none)'}\n\n"
            f"Latest user feedback to apply now:\n{pending_user_feedback or '(none — initial draft)'}\n\n"
            "Return the full updated design_diagram and design_justification."
        ),
    )

    diagram = proposal.get("design_diagram") or diagram
    justification = proposal.get("design_justification") or justification
    changes = (proposal.get("changes_made") or "").strip()
    assistant_message = (
        proposal.get("assistant_message")
        or ("Here is the first draft of the system design." if first_draft else "Design updated.")
    )
    if changes and changes.lower() not in assistant_message.lower():
        assistant_message = f"{assistant_message}\n\nChanges: {changes}"

    new_messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": assistant_message, "node": "system_design"}
    ]

    resume = interrupt(
        {
            "phase": "system_design",
            "assistant_message": assistant_message,
            "design_diagram": diagram,
            "design_justification": justification,
            "business_spec": business_spec,
            "can_approve": True,
            "can_download_final": False,
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()

    if action == "approve":
        msgs = list(new_messages)
        if user_text:
            msgs.append({"role": "user", "content": user_text, "node": "system_design"})
        publish_msg = (
            "Design version approved. Sending the design package (spec + diagram + "
            "justification) to the Software System Manager agent. You can keep chatting "
            "to refine and approve again anytime."
        )
        msgs.append({"role": "assistant", "content": publish_msg, "node": "system_design"})
        # Stay in system_design so the user can revise and approve updated versions again.
        return {
            "design_diagram": diagram,
            "design_justification": justification,
            "design_approved": False,
            "publish_requested": True,
            "phase": "system_design",
            "pending_user_feedback": "",
            "messages": msgs,
            "pending_assistant_message": publish_msg,
        }

    msgs = list(new_messages)
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "system_design"})
    return {
        "design_diagram": diagram,
        "design_justification": justification,
        "design_approved": False,
        "phase": "system_design",
        "pending_user_feedback": user_text,
        "messages": msgs,
        "pending_assistant_message": assistant_message,
    }
