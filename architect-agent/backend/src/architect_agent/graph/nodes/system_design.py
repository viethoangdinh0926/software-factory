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
    """Node 2: propose + refine design until the user finalizes the session."""
    if state.get("design_approved"):
        return {"phase": "done"}

    business_spec = state.get("business_spec") or ""
    diagram = state.get("design_diagram") or ""
    justification = state.get("design_justification") or ""
    new_messages: list[dict[str, Any]] = []

    while True:
        history_tail = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in new_messages[-10:]
        )
        proposal = _invoke_json(
            system=(
                "You are the Architect agent's system design node.\n"
                "Propose a high-level design (monolithic or distributed) with a Mermaid diagram "
                "and a clear justification for every component.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "design_diagram": string,\n'
                '  "design_justification": string,\n'
                '  "assistant_message": string,\n'
                '  "style": "monolithic" | "distributed"\n'
                "}\n"
                "design_diagram must be raw Mermaid (no markdown fences)."
            ),
            user=(
                f"Approved business specification:\n\n{business_spec}\n\n"
                f"Current diagram:\n{diagram or '(none)'}\n\n"
                f"Current justification:\n{justification or '(none)'}\n\n"
                f"Recent design turns:\n{history_tail or '(none)'}\n\n"
                "Create or revise the design. Explain trade-offs briefly in assistant_message."
            ),
        )

        diagram = proposal.get("design_diagram") or diagram
        justification = proposal.get("design_justification") or justification
        assistant_message = proposal.get("assistant_message") or "Here is the proposed design."
        new_messages.append(
            {"role": "assistant", "content": assistant_message, "node": "system_design"}
        )

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
            if user_text:
                new_messages.append(
                    {"role": "user", "content": user_text, "node": "system_design"}
                )
            done_msg = "System design finalized for this design session."
            new_messages.append(
                {"role": "assistant", "content": done_msg, "node": "system_design"}
            )
            return {
                "design_diagram": diagram,
                "design_justification": justification,
                "design_approved": True,
                "phase": "done",
                "messages": new_messages,
                "pending_assistant_message": done_msg,
            }

        if user_text:
            new_messages.append({"role": "user", "content": user_text, "node": "system_design"})
        # loop → revise design
