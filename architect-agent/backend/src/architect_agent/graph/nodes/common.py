"""Shared LLM helpers for principal-architect graph nodes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from architect_agent.context_budget import EXPLANATION_DEPTH_DIGEST
from architect_agent.json_util import (
    parse_llm_json_object,
    recover_architecture_payload_from_prose,
)
from architect_agent.llm import get_chat_model
from architect_agent.query_intent import (
    extract_http_endpoints,
    format_agreed_endpoints,
    wants_endpoint_list,
    with_resolution_close,
)

logger = logging.getLogger(__name__)

ProseRecover = Callable[[str], dict[str, Any] | None]

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`.\n"
    "No markdown, no ``` fences, no bare Mermaid.\n"
    "Fill THIS STEP's primary artifact in full (never empty). Other large fields \"\".\n"
    "Mermaid only in design_diagram_lines as short strings.\n"
    "assistant_message: keep the full elaborated justification (what changed, why, "
    "alternatives rejected, trade-offs accepted); escape its newlines as \\n. Invite Approve."
)

_QA_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object starting with `{`.\n"
    '{"assistant_message": "<full answer to the user question>"}\n'
    "Do not invite Approve. Do not rewrite artifacts."
)


def invoke_json(
    system: str,
    user: str,
    *,
    recover_prose: ProseRecover | None = None,
    prefer_prose: bool = False,
    retry_hint: str | None = None,
) -> dict[str, Any]:
    """Invoke the chat model and parse a JSON object, with retry + prose fallback."""
    model = get_chat_model()
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    content = _invoke_content(model, messages)
    try:
        return parse_llm_json_object(content)
    except ValueError as first_exc:
        if prefer_prose and recover_prose is not None:
            recovered_pref = recover_prose(content)
            if recovered_pref is not None:
                logger.warning(
                    "LLM JSON parse failed; using caller prose recovery: %s",
                    first_exc,
                )
                return recovered_pref

        recovered = recover_architecture_payload_from_prose(content)
        if recovered is not None and recovered.get("design_diagram_lines"):
            logger.warning(
                "LLM JSON parse failed; recovered Mermaid from prose: %s",
                first_exc,
            )
            return recovered

        logger.warning("LLM JSON parse failed; retrying once: %s", first_exc)
        retry_messages = [
            SystemMessage(content=system),
            HumanMessage(content=user),
            SystemMessage(content=retry_hint or _RETRY_HINT),
            HumanMessage(
                content=(
                    "Retry now. Output MUST start with `{`. Preview of your bad reply "
                    f"(truncated):\n{content[:900]}"
                )
            ),
        ]
        content2 = _invoke_content(model, retry_messages)
        try:
            return parse_llm_json_object(content2)
        except ValueError as second_exc:
            for blob in (content2, content):
                if recover_prose is not None:
                    recovered_custom = recover_prose(blob)
                    if recovered_custom is not None:
                        logger.warning(
                            "LLM JSON parse failed after retry; using caller prose recovery: %s",
                            second_exc,
                        )
                        return recovered_custom
            recovered2 = recover_architecture_payload_from_prose(content2)
            if recovered2 is not None:
                logger.warning(
                    "LLM JSON parse failed after retry; using prose recovery: %s",
                    second_exc,
                )
                return recovered2
            if recovered is not None:
                return recovered
            raise ValueError(
                f"LLM JSON parse failed after retry: {second_exc}"
            ) from first_exc


def _invoke_content(model: Any, messages: list[Any]) -> str:
    response = model.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def _qa_recover(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    return {"assistant_message": body}


def _artifacts_for_qa(state: dict[str, Any]) -> str:
    parts = [
        f"Phase: {state.get('phase') or ''}",
        f"Track: {state.get('design_track') or ''} step {state.get('design_step') or 0}",
        f"Business spec:\n{(state.get('business_spec') or '')[:6000]}",
        f"Trade-off ledger:\n{(state.get('tradeoff_ledger') or '')[:2500]}",
        f"Scale estimates:\n{(state.get('scale_estimates') or '')[:2500]}",
        f"Core microservices:\n{(state.get('api_contracts') or '')[:6000]}",
        f"Communication schemes:\n{(state.get('communication_schemes') or '')[:4000]}",
        f"FMEA notes:\n{(state.get('fmea_notes') or '')[:2500]}",
        f"Design diagram:\n{(state.get('design_diagram') or '')[:3000]}",
        f"Design justification:\n{(state.get('design_justification') or '')[:2500]}",
        f"Market grade: {state.get('market_evaluation_grade') or '(none)'}",
        f"Market report:\n{(state.get('market_evaluation_report') or '')[:4000]}",
    ]
    return "\n\n".join(parts)


def answer_open_query(state: dict[str, Any], question: str, *, node: str) -> str:
    """Answer a user question from current artifacts without rewriting the step."""
    q = (question or "").strip()
    apis = str(state.get("api_contracts") or "")
    if wants_endpoint_list(q):
        return format_agreed_endpoints(
            extract_http_endpoints(apis, str(state.get("design_justification") or ""))
        )
    grade = str(state.get("market_evaluation_grade") or "").strip()
    if grade and "grade" in q.lower():
        return f"The market evaluation grade for this design version is **{grade}**."
    result = invoke_json(
        system=(
            "Answer the user's question from the current artifacts. This is Q&A "
            "before they approve this workflow step.\n"
            f"Current node: {node}.\n"
            "Use concrete facts (numbers, service names, owned objects, communication "
            "schemes/protocols, CAP choices).\n"
            f"{EXPLANATION_DEPTH_DIGEST}\n"
            "This turn is an ANSWER, not an artifact rewrite: go beyond stating the current "
            "value. Explain the reasoning behind it — the forces that drove it, the "
            "alternatives rejected, the trade-offs accepted — and name the relevant pattern "
            "or principle so the user leaves understanding the architecture.\n"
            "Do not invite Approve. Do not say you finalized or updated the design.\n"
            "Do not rewrite artifacts. assistant_message is the full answer.\n"
            'Respond ONLY with JSON: {"assistant_message": string}'
        ),
        user=f"{_artifacts_for_qa(state)}\n\nUser question:\n{q}",
        recover_prose=_qa_recover,
        prefer_prose=True,
        retry_hint=_QA_RETRY_HINT,
    )
    return str(result.get("assistant_message") or "").strip() or (
        "I can answer from the current artifacts on this step. "
        "Ask about a specific section, or click Approve when you are ready to continue."
    )


def answer_before_approve(
    state: dict[str, Any],
    user_text: str,
    *,
    node: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Stay on the current wait interrupt after answering; keep Approve enabled."""
    answer = with_resolution_close(
        answer_open_query(state, user_text, node=node),
        changed=False,
    )
    return {
        **base,
        "pending_user_feedback": "",
        "pending_assistant_message": answer,
        "stay_on_interrupt": True,
        "publish_requested": False,
        "messages": [
            {"role": "user", "content": user_text, "node": node},
            {"role": "assistant", "content": answer, "node": node},
        ],
    }


APPROVE_LABELS = {
    ("phase0", 0): "Confirm scope & start track",
    ("lld", 1): "Confirm gathering → blueprint",
    ("lld", 2): "Confirm blueprint → verification",
    ("lld", 3): "Approve & send design",
    ("hld", 1): "Confirm requirements → domain model",
    ("hld", 2): "Confirm domain → microservices",
    ("hld", 3): "Confirm services → communication & diagram",
    ("hld", 4): "Confirm diagram → FMEA",
    ("hld", 5): "Confirm FMEA → synthesis",
    ("hld", 6): "Approve & send design",
    ("market_research", 0): "Continue after market evaluation",
}

HLD_STEP_TITLES = {
    1: "Requirements & capacity estimation",
    2: "Domain object modeling",
    3: "Core microservices",
    4: "Communication schemes, infrastructure & system diagram",
    5: "Vulnerability & edge-case analysis (FMEA)",
    6: "Session synthesis & wrap-up",
}

LLD_STEP_TITLES = {
    1: "Information gathering",
    2: "Architectural blueprint",
    3: "Verification",
}


def design_step_title(phase: str, track: str, step: int) -> str:
    if phase == "market_research":
        return "Market evaluation"
    if phase == "phase0" or track == "unset":
        return "Scope classification"
    if track == "lld":
        return LLD_STEP_TITLES.get(step, "Low-level design")
    if track == "hld":
        return HLD_STEP_TITLES.get(step, "High-level design")
    return ""


def approve_label(phase: str, track: str, step: int, *, design_ready: bool = False) -> str:
    if phase == "market_research":
        return APPROVE_LABELS[("market_research", 0)]
    if phase == "lld":
        return APPROVE_LABELS.get(("lld", step), "Continue")
    if phase == "hld":
        return APPROVE_LABELS.get(("hld", step), "Continue")
    if phase == "phase0":
        return APPROVE_LABELS[("phase0", 0)]
    if design_ready:
        return "Approve & send design"
    return "Continue"


def is_design_approve_step(track: str, step: int) -> bool:
    return (track == "lld" and step >= 3) or (track == "hld" and step >= 6)
