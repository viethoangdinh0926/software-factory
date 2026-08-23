"""Durable discussion memory: keep settled decisions when chat tails are truncated."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from engineer_agent.config import get_settings
from engineer_agent.json_util import parse_llm_json_object

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4

DISCUSSION_MEMORY_RULES = """
DISCUSSION MEMORY (durable session memory — honor it over a short chat tail):
- A DISCUSSION MEMORY / CONVERSATION KEYNOTES block is the briefing of THIS
  sub-engineer chat: decisions, approvals, rejections, and open questions.
- A DISCUSSION MEMORY block in the user prompt is the long-term record of THIS
  sub-engineer / this discussion phase, not a peer's conversation.
- The labeled Latest user message is the work of this turn. Memory and the recent
  transcript are context — never ignore that message to restart planning or replay
  a canned status.
- NEVER re-open a settled decision or re-suggest a solution the user already
  accepted or rejected (including blocked-issue instructions already recorded).
- If you ask a question, name a concrete choice for THIS sub-engineer (which field,
  which plan item, which instruction) and a recommended default. Never "tell me more"
  or "I need more information" without naming the missing fact.
- When memory grows, keep locked decisions, settled issues, and rejected proposals;
  drop interview chatter.
""".strip()

_SKIP_DIGEST_PENDING = {
    "ok",
    "okay",
    "looks good",
    "lgtm",
    "approve",
    "approved",
    "next step",
    "continue",
    "sounds good",
}


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def fit_text(text: str, max_tokens: int, *, label: str = "content") -> str:
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
    max_tokens: int = 1200,
    max_turns: int = 8,
) -> str:
    turns = list(messages)[-max(1, max_turns) :]
    lines = [f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}" for m in turns]
    while lines and estimate_tokens("\n".join(lines)) > max_tokens:
        lines.pop(0)
    if not lines:
        return "(none)"
    return "\n".join(lines)


def format_phase_context(
    digest: str,
    messages: Iterable[dict[str, Any]],
    node: str = "phase",
    *,
    max_tokens: int = 1200,
    max_turns: int = 8,
) -> str:
    tail = format_history_tail(messages, max_tokens=max_tokens, max_turns=max_turns)
    digest_s = (digest or "").strip()
    parts: list[str] = []
    if digest_s:
        parts.append(
            "CONVERSATION KEYNOTES (decisions, approvals, rejections — do not "
            "re-open settled items or re-suggest rejected solutions):\n"
            f"{digest_s}"
        )
    parts.append(f"Recent {node} turns:\n{tail}")
    return "\n\n".join(parts)


def conversation_turn_user(
    digest: str,
    messages: Iterable[dict[str, Any]],
    node: str,
    pending: str,
    *,
    artifacts: str = "",
    empty_pending_note: str = "(none)",
    max_tokens: int = 1200,
    max_turns: int = 8,
) -> str:
    """Standard user blob: discussion memory, recent tail, artifacts, labeled latest message."""
    parts: list[str] = [
        format_phase_context(
            digest, messages, node, max_tokens=max_tokens, max_turns=max_turns
        )
    ]
    art = (artifacts or "").strip()
    if art:
        parts.append(art)
    body = (pending or "").strip()
    parts.append(f"Latest user message:\n{body or empty_pending_note}")
    return "\n\n".join(parts)


_TURN_KINDS = frozenset(
    {"answer", "concern", "complement", "approve", "disapprove", "unrelated", "unclear"}
)

_CONSULT_SYSTEM = """
You are the conversation keynotes consultant for ONE isolated chat session.
Judge the latest user message against the previous assistant message as a human would.
Do not decide from isolated keywords.

relevant=true when they answered a question, addressed a concern, complemented or
changed the idea, or approved/disapproved something in that prior message or the
live proposal.
vague=true when the reply is too unclear to act on (even if it might be related).
relevant=false when it is off-topic and does not engage the prior message.

If relevant and not vague: rewrite conversation keynotes as a brief briefing of THIS
chat — settled decisions, approvals, rejections, open questions. Keep prior keynotes.
Add this turn in your own words. Never quote the user verbatim.

If not relevant or vague: leave keynotes unchanged. Write clarify_message that names
the immediate open concern from the previous assistant message and asks them to
address it or clarify what they meant. Do not quote their message. Do not restart
planning. Do not stall with "tell me more".

Respond ONLY with JSON:
{
  "relevant": boolean,
  "vague": boolean,
  "kind": "answer" | "concern" | "complement" | "approve" | "disapprove" | "unrelated" | "unclear",
  "keynotes": string,
  "clarify_message": string
}
""".strip()


@dataclass
class UserTurnConsult:
    relevant: bool
    vague: bool
    kind: str
    keynotes: str
    clarify_message: str

    @property
    def needs_clarification(self) -> bool:
        return (not self.relevant) or self.vague


def consult_user_turn(
    *,
    pending: str,
    last_assistant: str,
    keynotes: str,
    phase: str = "",
) -> UserTurnConsult:
    pending_s = (pending or "").strip()
    prior = (keynotes or "").strip()
    last = (last_assistant or "").strip()
    fallback = UserTurnConsult(
        relevant=True,
        vague=False,
        kind="complement",
        keynotes=prior,
        clarify_message="",
    )
    if not pending_s or not last:
        return fallback
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from engineer_agent.llm import get_chat_model

        model = get_chat_model()
        response = model.invoke(
            [
                SystemMessage(content=_CONSULT_SYSTEM),
                HumanMessage(
                    content=(
                        f"Phase: {phase or '(n/a)'}\n\n"
                        f"Previous assistant message:\n{last[:4000]}\n\n"
                        f"Latest user message:\n{pending_s}\n\n"
                        f"Current conversation keynotes:\n{prior or '(none)'}\n"
                    )
                ),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        payload = parse_llm_json_object(str(content))
    except Exception:
        logger.exception("consult_user_turn failed; treating the turn as relevant")
        return fallback
    relevant = bool(payload.get("relevant", True))
    vague = bool(payload.get("vague", False))
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _TURN_KINDS:
        kind = "unclear" if (vague or not relevant) else "complement"
    updated = str(payload.get("keynotes") or "").strip()
    clarify = str(payload.get("clarify_message") or "").strip()
    if (not relevant) or vague:
        if not clarify:
            clarify = (
                "Please address the open point in my previous message, or clarify "
                "what you meant, so I can continue this conversation."
            )
        return UserTurnConsult(
            relevant=relevant,
            vague=vague,
            kind=kind if kind in {"unrelated", "unclear"} else ("unclear" if vague else "unrelated"),
            keynotes=prior,
            clarify_message=clarify,
        )
    return UserTurnConsult(
        relevant=True,
        vague=False,
        kind=kind,
        keynotes=updated or prior,
        clarify_message="",
    )


def refresh_discussion_digest(
    prior: str,
    *,
    pending: str = "",
    assistant: str = "",
    phase: str = "",
    extra: str = "",
) -> str:
    merged = _heuristic_digest_merge(
        prior, pending=pending, assistant=assistant, phase=phase
    )
    settings = get_settings()
    soft = getattr(settings, "context_digest_soft_tokens", 900)
    hard = getattr(settings, "context_digest_hard_tokens", 1400)
    target = getattr(settings, "context_digest_compact_target_tokens", 700)
    if estimate_tokens(merged) <= soft:
        return merged
    logger.info(
        "Compacting discussion_digest (%s tokens > soft limit %s)",
        estimate_tokens(merged),
        soft,
    )
    compacted = _llm_compact_digest(
        merged,
        pending=pending,
        assistant=assistant,
        phase=phase,
        extra=extra,
        target_tokens=target,
    )
    if estimate_tokens(compacted) > hard:
        compacted = fit_text(compacted, hard, label="discussion_digest")
    return compacted


def _heuristic_digest_merge(
    prior: str,
    *,
    pending: str,
    assistant: str,
    phase: str,
) -> str:
    prior_s = (prior or "").strip()
    additions: list[str] = []
    del pending  # user-turn briefing is owned by consult_user_turn keynotes
    assistant_s = (assistant or "").strip()
    if assistant_s and estimate_tokens(assistant_s) > 40:
        snippet = re.sub(r"\s+", " ", assistant_s)[:280]
        additions.append(f"- Last note: {snippet}")
    prior_l = prior_s.lower()
    new_lines = [line for line in additions if line.lower() not in prior_l]
    if not new_lines:
        return prior_s
    if not prior_s:
        return "## Settled decisions\n" + "\n".join(new_lines)
    return f"{prior_s}\n" + "\n".join(new_lines)


def _llm_compact_digest(
    digest: str,
    *,
    pending: str,
    assistant: str,
    phase: str,
    extra: str,
    target_tokens: int,
) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from engineer_agent.llm import get_chat_model

    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress discussion memory for a long engineering conversation.\n"
                    "Keep locked decisions, settled issues, blocked-issue instructions, "
                    "and rejected proposals. Never drop a decision the user already closed.\n"
                    "Use sections: Settled decisions, Issues raised and how resolved, "
                    "Rejected proposals, Open questions.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    'Respond ONLY with JSON: {"discussion_digest": string}.'
                )
            ),
            HumanMessage(
                content=(
                    f"Prior discussion memory:\n{digest}\n\n"
                    f"Phase: {phase or '(n/a)'}\n"
                    f"Latest user message:\n{pending or '(none)'}\n\n"
                    f"Latest assistant note:\n{(assistant or '')[:800] or '(none)'}\n\n"
                    f"Artifact excerpt:\n{(extra or '')[:1200]}\n"
                )
            ),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    try:
        payload = parse_llm_json_object(str(content))
    except (ValueError, json.JSONDecodeError):
        payload = {}
    return str(payload.get("discussion_digest") or digest).strip() or digest
