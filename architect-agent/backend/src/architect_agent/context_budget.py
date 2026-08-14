from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from architect_agent.config import get_settings
from architect_agent.json_util import parse_llm_json_object

logger = logging.getLogger(__name__)

# Approx chars-per-token for English/markdown budgeting (conservative).
_CHARS_PER_TOKEN = 4

# Compact grill-me digest — keeps interview rules without shipping the full skill file
# on every turn (frontmatter + prose). Full skill remains on disk for humans/docs.
GRILL_ME_DIGEST = """
Grill-me interview rules:
1. Ask exactly ONE question per turn; wait for the answer.
2. Prefer foundational decisions first (actors → jobs → scope → constraints → non-goals).
3. Offer a (Recommended) answer; challenge vague language until precise.
4. Capture durable decisions into the living business-spec markdown (no raw Q&A logs).
5. ready_for_design=true only when the spec covers: problem, actors/jobs, v1 scope,
   explicit non-goals, critical invariants, success criteria, major assumptions/risks
   — OR when the user explicitly asks to stop questioning / approve / move on.
6. User may keep adding detail after ready; only they approve advancing.
7. NEVER repeat or lightly rephrase a question already asked. If a topic was asked,
   move to a different uncovered checklist topic — or mark ready if none remain.
8. Prefer the next uncovered checklist topic over deepening a topic that already has
   a usable answer in the living spec.
9. If the user explicitly says to stop asking, approve, or that they are done/ready,
   stop questioning immediately. If the living spec is too thin to sketch a design,
   say so honestly and list the gaps (do not enable approval unless they say
   "approve anyway"). If the spec is sufficient, tell them to click "Approve business spec".

Question format:
❓ **<short title>**: <question>
➡️ (Recommended) <recommended answer>
""".strip()

_SPEC_SECTIONS = (
    "Problem",
    "Actors",
    "Goals",
    "In scope (v1)",
    "Out of scope",
    "Critical invariants",
    "Success criteria",
    "Assumptions & risks",
)


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def fit_text(text: str, max_tokens: int, *, label: str = "content") -> str:
    """Hard-cap text for prompt safety. Prefer calling compact_* before this."""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    budget = max_tokens * _CHARS_PER_TOKEN
    keep = max(0, budget - 80)
    trimmed = text[:keep].rstrip()
    return (
        f"{trimmed}\n\n…\n"
        f"[{label} truncated to ~{max_tokens} tokens for context budget]"
    )


def format_history_tail(
    messages: Iterable[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    max_turns: int | None = None,
) -> str:
    """Build a recent transcript that fits the history token budget."""
    settings = get_settings()
    token_budget = max_tokens if max_tokens is not None else settings.context_history_max_tokens
    turn_budget = max_turns if max_turns is not None else settings.context_history_max_turns

    turns = list(messages)[-max(1, turn_budget) :]
    lines = [f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}" for m in turns]
    # Drop oldest lines until under budget.
    while lines and estimate_tokens("\n".join(lines)) > token_budget:
        lines.pop(0)
    if not lines:
        return "(none)"
    return "\n".join(lines)


def maybe_compact_business_spec(spec: str, *, force: bool = False) -> str:
    """
    If the living spec exceeds the soft token limit, ask the LLM to rewrite it into a
    concise structured document. Falls back to hard truncation only if still oversized.
    """
    settings = get_settings()
    soft = settings.context_spec_soft_tokens
    hard = settings.context_spec_hard_tokens
    target = settings.context_spec_compact_target_tokens

    tokens = estimate_tokens(spec)
    if not force and tokens <= soft:
        return spec

    logger.info(
        "Compacting business_spec (%s tokens > soft limit %s; target %s)",
        tokens,
        soft,
        target,
    )
    compacted = _llm_compact_spec(spec, target_tokens=target)
    if estimate_tokens(compacted) > hard:
        logger.warning(
            "Compacted spec still over hard limit (%s > %s); truncating",
            estimate_tokens(compacted),
            hard,
        )
        compacted = fit_text(compacted, hard, label="business_spec")
    return compacted


def maybe_compact_design_justification(text: str) -> str:
    settings = get_settings()
    soft = settings.context_justification_soft_tokens
    hard = settings.context_justification_hard_tokens
    if estimate_tokens(text) <= soft:
        return text
    logger.info(
        "Compacting design_justification (%s tokens > soft limit %s)",
        estimate_tokens(text),
        soft,
    )
    compacted = _llm_compact_justification(text, target_tokens=settings.context_justification_compact_target_tokens)
    if estimate_tokens(compacted) > hard:
        compacted = fit_text(compacted, hard, label="design_justification")
    return compacted


def _llm_compact_spec(spec: str, *, target_tokens: int) -> str:
    from architect_agent.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    sections = ", ".join(f"## {s}" for s in _SPEC_SECTIONS)
    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress a living business specification for a long interview session.\n"
                    "Preserve every durable decision, actor, scope item, invariant, metric, and risk.\n"
                    "Remove repetition, interview chatter, and 'notes' appendices.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    f"Use these section headings when content exists: {sections}.\n"
                    'Respond ONLY with JSON: {"updated_business_spec": string}.'
                )
            ),
            HumanMessage(content=f"Spec to compress:\n\n{spec}"),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = _parse_json_object(str(content))
    return str(payload.get("updated_business_spec") or spec).strip() or spec


def _llm_compact_justification(text: str, *, target_tokens: int) -> str:
    from architect_agent.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress a system-design justification markdown.\n"
                    "Keep one short rationale per component; drop duplicates.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    'Respond ONLY with JSON: {"design_justification": string}.'
                )
            ),
            HumanMessage(content=f"Justification to compress:\n\n{text}"),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = _parse_json_object(str(content))
    return str(payload.get("design_justification") or text).strip() or text


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return parse_llm_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return {}
