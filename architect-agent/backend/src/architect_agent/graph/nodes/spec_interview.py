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
    """One interview turn per invoke; loops via graph until user approves."""
    if state.get("spec_approved"):
        return {"phase": "system_design", "pending_user_feedback": ""}

    grill = _load_grill_me()
    business_spec = state.get("business_spec") or ""
    pending_user_feedback = state.get("pending_user_feedback") or ""
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "spec_interview"]
    history_tail = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in prior[-16:])

    if pending_user_feedback:
        fold = _invoke_json(
            system=(
                "Update the business specification markdown from one interview answer. "
                'Return JSON: {"updated_business_spec": string}.'
            ),
            user=(
                f"Spec:\n{business_spec}\n\n"
                f"User answer:\n{pending_user_feedback}\n"
            ),
        )
        business_spec = fold.get("updated_business_spec") or business_spec

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
            f"Latest user answer already folded into the spec (if any):\n"
            f"{pending_user_feedback or '(none)'}\n\n"
            "If not ready, ask exactly ONE grill-me question with a recommended answer. "
            "If ready, summarize readiness and invite approval — the user may still add detail."
        ),
    )

    business_spec = assessment.get("updated_business_spec") or business_spec
    assistant_message = assessment.get("assistant_message") or "Please share more detail."
    ready = bool(assessment.get("ready_for_design"))
    new_messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": assistant_message, "node": "spec_interview"}
    ]

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
            return {
                "business_spec": business_spec,
                "ready_for_design": False,
                "phase": "spec_interview",
                "pending_user_feedback": "",
                "messages": new_messages
                + [{"role": "assistant", "content": deny, "node": "spec_interview"}],
                "pending_assistant_message": deny,
            }

        msgs = list(new_messages)
        if user_text:
            msgs.append({"role": "user", "content": user_text, "node": "spec_interview"})
        transfer = "Business specification approved. Transferring to system design."
        msgs.append({"role": "assistant", "content": transfer, "node": "spec_interview"})
        return {
            "business_spec": business_spec,
            "ready_for_design": True,
            "spec_approved": True,
            "phase": "system_design",
            "pending_user_feedback": "",
            "messages": msgs,
            "pending_assistant_message": transfer,
        }

    msgs = list(new_messages)
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "spec_interview"})
    return {
        "business_spec": business_spec,
        "ready_for_design": ready,
        "phase": "spec_interview",
        "pending_user_feedback": user_text,
        "messages": msgs,
        "pending_assistant_message": assistant_message,
    }
