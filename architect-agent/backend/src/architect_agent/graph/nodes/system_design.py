from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from architect_agent.context_budget import (
    format_history_tail,
    maybe_compact_business_spec,
    maybe_compact_design_justification,
)
from architect_agent.graph.state import DesignGraphState
from architect_agent.json_util import coerce_diagram_text, parse_llm_json_object
from architect_agent.llm import get_chat_model
from architect_agent.mermaid_sanitize import sanitize_mermaid


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
    return parse_llm_json_object(str(content))


def system_design_node(state: DesignGraphState) -> dict[str, Any]:
    """Propose/revise the living design once from spec + pending user feedback."""
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    diagram = state.get("design_diagram") or ""
    justification = maybe_compact_design_justification(state.get("design_justification") or "")
    pending_user_feedback = state.get("pending_user_feedback") or ""
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "system_design"]
    history_tail = format_history_tail(prior)

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
            "Keep design_justification concise: one short section per component.\n"
            "On every turn after the first draft, treat user chat as design change requests.\n"
            "Respond ONLY with a single JSON object (no markdown fences).\n"
            "Prefer design_diagram_lines (array of Mermaid lines) to avoid broken JSON.\n"
            "If you use design_diagram as one string, escape newlines as \\n and quotes as \\\".\n"
            "Schema:\n"
            "{\n"
            '  "design_diagram_lines": [string, ...],\n'
            '  "design_diagram": string,\n'
            '  "design_justification": string,\n'
            '  "assistant_message": string,\n'
            '  "style": "monolithic" | "distributed",\n'
            '  "changes_made": string\n'
            "}\n"
            "Provide at least one of design_diagram_lines or design_diagram (raw Mermaid).\n"
            "Mermaid label rules (critical):\n"
            "- Prefer flowchart LR or TD.\n"
            "- If a node/edge label contains parentheses, brackets, braces, slashes, or &\n"
            '  wrap the label in double quotes, e.g. B{"Agent (LLM)"} or A -->|"sync / async"| B.\n'
            "- Never put unquoted (...) inside [], {}, or () shape delimiters.\n"
            "- Keep node IDs short alphanumeric (A, B, ApiGateway).\n"
            "- Use a *different* light fill for each node from this palette:\n"
            "  #b8d4f0, #c8f0d8, #ffe2b8, #e2d0ff, #ffd0d8, #d8f0f0, #fff0a8, #d0e8ff\n"
            "  Example: style A fill:#b8d4f0,stroke:#4a5a70,color:#000000\n"
            "  Example: style B fill:#c8f0d8,stroke:#4a5a70,color:#000000\n"
            "  Avoid dark fills — labels must stay black and readable.\n"
            "  Always emit one style line per node."
        ),
        user=(
            f"{mode}\n\n"
            f"Approved business specification:\n\n{business_spec}\n\n"
            f"Current diagram:\n{diagram or '(none — produce first draft)'}\n\n"
            f"Current justification:\n{justification or '(none)'}\n\n"
            f"Design conversation so far:\n{history_tail}\n\n"
            f"Latest user feedback to apply now:\n{pending_user_feedback or '(none — initial draft)'}\n\n"
            "Return the full updated design_diagram and design_justification."
        ),
    )

    diagram = sanitize_mermaid(coerce_diagram_text(proposal, fallback=diagram))
    justification = proposal.get("design_justification") or justification
    justification = maybe_compact_design_justification(justification)
    changes = (proposal.get("changes_made") or "").strip()
    assistant_message = (
        proposal.get("assistant_message")
        or ("Here is the first draft of the system design." if first_draft else "Design updated.")
    )
    if changes and changes.lower() not in assistant_message.lower():
        assistant_message = f"{assistant_message}\n\nChanges: {changes}"

    return {
        "design_diagram": diagram,
        "design_justification": justification,
        "design_approved": False,
        "phase": "system_design",
        "pending_user_feedback": "",
        "publish_requested": False,
        "pending_assistant_message": assistant_message,
        "messages": [
            {"role": "assistant", "content": assistant_message, "node": "system_design"}
        ],
    }


def design_wait_node(state: DesignGraphState) -> dict[str, Any]:
    """Pause for design chat/approve; queue feedback or a publish request."""
    business_spec = state.get("business_spec") or ""
    diagram = state.get("design_diagram") or ""
    justification = state.get("design_justification") or ""
    assistant_message = state.get("pending_assistant_message") or ""

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
        publish_msg = (
            "Design version approved. Sending the design package (spec + diagram + "
            "justification) to the Software System Manager agent. You can keep chatting "
            "to refine and approve again anytime."
        )
        msgs: list[dict[str, Any]] = []
        if user_text:
            msgs.append({"role": "user", "content": user_text, "node": "system_design"})
        msgs.append({"role": "assistant", "content": publish_msg, "node": "system_design"})
        return {
            "design_diagram": diagram,
            "design_justification": justification,
            "design_approved": False,
            "publish_requested": True,
            "phase": "system_design",
            "pending_user_feedback": "",
            "pending_assistant_message": publish_msg,
            "messages": msgs,
        }

    msgs = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "system_design"})
    return {
        "design_diagram": diagram,
        "design_justification": justification,
        "design_approved": False,
        # Clear any prior publish latch so routing goes back to generate on chat.
        "publish_requested": False,
        "phase": "system_design",
        "pending_user_feedback": user_text,
        "messages": msgs,
    }
