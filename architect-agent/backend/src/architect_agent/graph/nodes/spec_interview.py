from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from architect_agent.config import get_settings
from architect_agent.graph.state import DesignGraphState
from architect_agent.llm import get_chat_model

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _load_grill_me() -> str:
    return Path(get_settings().grill_me_skill_path).read_text(encoding="utf-8")


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


def spec_interview_node(state: DesignGraphState) -> dict[str, Any]:
    """Node 1: grill-me interview until the user approves advancing to design."""
    if state.get("spec_approved"):
        return {"phase": "system_design"}

    grill = _load_grill_me()
    business_spec = state.get("business_spec") or ""
    new_messages: list[dict[str, Any]] = []
    ready = bool(state.get("ready_for_design"))

    while True:
        history_tail = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in new_messages[-10:]
        )
        assessment = _invoke_json(
            system=(
                "You are the Architect agent's specification interviewer "
                "(merged Business Analyst + Architect discovery).\n"
                "Follow the grill-me skill strictly.\n\n"
                f"{grill}\n\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "ready_for_design": boolean,\n'
                '  "updated_business_spec": string,\n'
                '  "assistant_message": string,\n'
                '  "rationale": string\n'
                "}"
            ),
            user=(
                f"Current business specification markdown:\n\n{business_spec}\n\n"
                f"Recent turns in this node:\n{history_tail or '(none)'}\n\n"
                "If not ready, ask exactly ONE grill-me question with a recommended answer. "
                "If ready, summarize readiness and invite approval — the user may still add detail."
            ),
        )

        business_spec = assessment.get("updated_business_spec") or business_spec
        assistant_message = assessment.get("assistant_message") or "Please share more detail."
        ready = bool(assessment.get("ready_for_design"))
        new_messages.append(
            {"role": "assistant", "content": assistant_message, "node": "spec_interview"}
        )

        resume = interrupt(
            {
                "phase": "spec_interview",
                "ready_for_design": ready,
                "assistant_message": assistant_message,
                "business_spec": business_spec,
                "can_approve": ready,
                "can_download_spec": True,
            }
        )

        action = (resume or {}).get("action", "chat")
        user_text = ((resume or {}).get("text") or "").strip()

        if action == "approve":
            if not ready:
                deny = (
                    "Not ready to advance yet — the readiness checklist is incomplete. "
                    "Continue the interview, then approve when the UI enables it."
                )
                new_messages.append(
                    {"role": "assistant", "content": deny, "node": "spec_interview"}
                )
                continue

            if user_text:
                new_messages.append(
                    {"role": "user", "content": user_text, "node": "spec_interview"}
                )
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "Business specification approved. Transferring to system design.",
                    "node": "spec_interview",
                }
            )
            return {
                "business_spec": business_spec,
                "ready_for_design": True,
                "spec_approved": True,
                "phase": "system_design",
                "messages": new_messages,
                "pending_assistant_message": "Business specification approved. Transferring to system design.",
            }

        if user_text:
            new_messages.append({"role": "user", "content": user_text, "node": "spec_interview"})
            fold = _invoke_json(
                system=(
                    "Update the business specification markdown from one interview answer. "
                    'Return JSON: {"updated_business_spec": string}.'
                ),
                user=(
                    f"Spec:\n{business_spec}\n\n"
                    f"Last assistant message:\n{assistant_message}\n\n"
                    f"User answer:\n{user_text}\n"
                ),
            )
            business_spec = fold.get("updated_business_spec") or business_spec
        # loop → ask next question / reassess
