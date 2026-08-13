from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from architect_agent.config import get_settings
from architect_agent.context_budget import (
    GRILL_ME_DIGEST,
    estimate_tokens,
    format_history_tail,
    maybe_compact_business_spec,
)
from architect_agent.graph.state import DesignGraphState
from architect_agent.interview_progress import (
    extract_question_titles,
    fallback_question,
    format_asked_block,
    format_uncovered_block,
    is_repeat_question,
    message_for_user_stop,
    uncovered_checklist,
    user_requests_approve_anyway,
    user_requests_ready,
)
from architect_agent.llm import get_chat_model

logger = logging.getLogger(__name__)

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


def spec_interview_node(state: DesignGraphState) -> dict[str, Any]:
    """Produce one interview assistant turn from current spec + pending user feedback."""
    if state.get("spec_approved"):
        return {"phase": "system_design", "pending_user_feedback": ""}

    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending_user_feedback = state.get("pending_user_feedback") or ""
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "spec_interview"]
    history_tail = format_history_tail(prior)
    asked_titles = extract_question_titles(prior)
    prior_assistant_texts = [
        str(m.get("content") or "") for m in prior if m.get("role") == "assistant"
    ]
    open_items = uncovered_checklist(business_spec, asked_titles)
    forced_ready = user_requests_ready(pending_user_feedback)

    if pending_user_feedback and not forced_ready:
        fold = _invoke_json(
            system=(
                "Update the living business specification markdown from one interview answer.\n"
                "Merge the answer into the correct sections. Keep the document concise.\n"
                "Do NOT append raw Q&A, interview transcripts, or growing 'notes' logs.\n"
                "Prefer rewriting bullets over adding new narrative paragraphs.\n"
                'Return JSON: {"updated_business_spec": string}.'
            ),
            user=(
                f"Spec:\n{business_spec}\n\n"
                f"User answer:\n{pending_user_feedback}\n"
            ),
        )
        business_spec = fold.get("updated_business_spec") or business_spec
        business_spec = maybe_compact_business_spec(business_spec)
        open_items = uncovered_checklist(business_spec, asked_titles)
    elif pending_user_feedback and forced_ready:
        # Still fold any incidental content, but don't block on LLM if this is only a control phrase.
        if len(pending_user_feedback.split()) > 8:
            try:
                fold = _invoke_json(
                    system=(
                        "Update the living business specification markdown from one interview answer.\n"
                        "Ignore pure process instructions like 'stop asking' / 'approve'.\n"
                        "Merge any real product decisions into the correct sections.\n"
                        'Return JSON: {"updated_business_spec": string}.'
                    ),
                    user=(
                        f"Spec:\n{business_spec}\n\n"
                        f"User answer:\n{pending_user_feedback}\n"
                    ),
                )
                business_spec = fold.get("updated_business_spec") or business_spec
                business_spec = maybe_compact_business_spec(business_spec)
            except Exception:  # noqa: BLE001
                logger.exception("Fold skipped after user requested ready")

    if forced_ready:
        assistant_message, ready = message_for_user_stop(
            business_spec,
            approve_anyway=user_requests_approve_anyway(pending_user_feedback),
        )
        return {
            "business_spec": business_spec,
            "ready_for_design": ready,
            "phase": "spec_interview",
            "pending_user_feedback": "",
            "pending_assistant_message": assistant_message,
            "publish_requested": False,
            "messages": [
                {
                    "role": "assistant",
                    "content": assistant_message,
                    "node": "spec_interview",
                }
            ],
        }

    assessment = _invoke_json(
        system=(
            "You are the Architect agent's specification interviewer "
            "(merged Business Analyst + Architect discovery).\n"
            f"{GRILL_ME_DIGEST}\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "ready_for_design": boolean,\n'
            '  "updated_business_spec": string,\n'
            '  "assistant_message": string,\n'
            '  "topic_id": string,\n'
            '  "rationale": string\n'
            "}\n"
            "updated_business_spec must stay concise and structured; never dump chat history into it.\n"
            "topic_id must be one of the uncovered checklist ids, or \"ready\" if marking ready.\n"
            "assistant_message must NOT rephrase any already-asked title."
        ),
        user=(
            f"Current business specification markdown:\n\n{business_spec}\n\n"
            f"Already asked question titles (DO NOT repeat or rephrase):\n"
            f"{format_asked_block(asked_titles)}\n\n"
            f"Uncovered checklist topics (ask the first/highest unless user answer unlocks ready):\n"
            f"{format_uncovered_block(open_items)}\n\n"
            f"Recent turns in this node:\n{history_tail}\n\n"
            f"Latest user answer already folded into the spec (if any):\n"
            f"{pending_user_feedback or '(none)'}\n\n"
            "If the user asked to stop questioning: do NOT ask another question. "
            "If the living spec is too thin to sketch a design, be honest about the gaps "
            "and set ready_for_design=false unless they said approve anyway.\n"
            "If uncovered topics remain, ask exactly ONE new question for the next uncovered topic.\n"
            "If none remain, set ready_for_design=true and invite approval — do not invent filler questions."
        ),
    )

    business_spec = assessment.get("updated_business_spec") or business_spec
    if estimate_tokens(business_spec) > get_settings().context_spec_soft_tokens:
        business_spec = maybe_compact_business_spec(business_spec)

    assistant_message = assessment.get("assistant_message") or "Please share more detail."
    ready = bool(assessment.get("ready_for_design"))
    if user_requests_ready(pending_user_feedback):
        assistant_message, ready = message_for_user_stop(
            business_spec,
            approve_anyway=user_requests_approve_anyway(pending_user_feedback),
        )

    # Guardrail: replace repeated/rephrased questions with the next uncovered checklist item.
    # Never override an explicit user request to stop/approve.
    if not user_requests_ready(pending_user_feedback):
        if not ready and is_repeat_question(
            assistant_message, asked_titles, prior_assistant_texts
        ):
            logger.info("Interview question looked like a repeat; using checklist fallback")
            assistant_message, ready = fallback_question(business_spec, asked_titles)
        elif ready and open_items and "❓" in assistant_message:
            assistant_message, ready = fallback_question(business_spec, asked_titles)
        elif (not ready) and not open_items and "❓" in assistant_message:
            assistant_message, ready = fallback_question(business_spec, asked_titles)

    return {
        "business_spec": business_spec,
        "ready_for_design": ready,
        "phase": "spec_interview",
        "pending_user_feedback": "",
        "pending_assistant_message": assistant_message,
        "publish_requested": False,
        "messages": [
            {"role": "assistant", "content": assistant_message, "node": "spec_interview"}
        ],
    }


def spec_wait_node(state: DesignGraphState) -> dict[str, Any]:
    """Pause for user chat/approve; write the next pending feedback or approval flags."""
    business_spec = state.get("business_spec") or ""
    ready = bool(state.get("ready_for_design"))
    assistant_message = state.get("pending_assistant_message") or ""

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
                "pending_assistant_message": deny,
                "messages": [
                    {"role": "assistant", "content": deny, "node": "spec_interview"}
                ],
            }

        transfer = (
            "Business specification approved. Researching popular alternatives "
            "and preparing a market evaluation report…"
        )
        msgs: list[dict[str, Any]] = []
        if user_text:
            msgs.append({"role": "user", "content": user_text, "node": "spec_interview"})
        msgs.append({"role": "assistant", "content": transfer, "node": "spec_interview"})
        return {
            "business_spec": business_spec,
            "ready_for_design": True,
            "spec_approved": True,
            "phase": "market_research",
            "pending_user_feedback": "",
            "pending_assistant_message": transfer,
            "messages": msgs,
        }

    msgs = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "spec_interview"})
    return {
        "business_spec": business_spec,
        "ready_for_design": ready,
        "phase": "spec_interview",
        "pending_user_feedback": user_text,
        "messages": msgs,
    }
