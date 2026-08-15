"""Shared LLM helpers for principal-architect graph nodes."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from architect_agent.json_util import (
    parse_llm_json_object,
    recover_architecture_payload_from_prose,
)
from architect_agent.llm import get_chat_model

logger = logging.getLogger(__name__)

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`.\n"
    "No markdown, no ``` fences, no bare Mermaid.\n"
    "Fill THIS STEP's primary artifact in full (never empty). Other large fields \"\".\n"
    "Mermaid only in design_diagram_lines as short strings.\n"
    "assistant_message ≤400 chars. Invite Approve."
)


def invoke_json(system: str, user: str) -> dict[str, Any]:
    model = get_chat_model()
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    content = _invoke_content(model, messages)
    try:
        return parse_llm_json_object(content)
    except ValueError as first_exc:
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
            SystemMessage(content=_RETRY_HINT),
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
            recovered2 = recover_architecture_payload_from_prose(content2)
            if recovered2 is not None:
                logger.warning(
                    "LLM JSON parse failed after retry; using prose recovery: %s",
                    second_exc,
                )
                return recovered2
            # Last chance: recover from the first reply even without Mermaid.
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


APPROVE_LABELS = {
    ("phase0", 0): "Confirm scope & start track",
    ("lld", 1): "Confirm gathering → blueprint",
    ("lld", 2): "Confirm blueprint → verification",
    ("lld", 3): "Approve & send design",
    ("hld", 1): "Confirm requirements → domain model",
    ("hld", 2): "Confirm domain → APIs",
    ("hld", 3): "Confirm APIs → infrastructure",
    ("hld", 4): "Confirm infrastructure → FMEA",
    ("hld", 5): "Confirm FMEA → synthesis",
    ("hld", 6): "Approve & send design",
    ("market_research", 0): "Continue after market evaluation",
}


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
