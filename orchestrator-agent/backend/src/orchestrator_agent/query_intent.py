"""Understand user turns as command (act) vs information (answer), via the LLM."""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_REVISION_HINTS = (
    "add ",
    "remove ",
    "delete ",
    "change ",
    "replace ",
    "rename ",
    "switch ",
    "instead",
    "new endpoint",
    "health check",
    "use grpc",
    "use rest",
    "use graphql",
    "drop ",
    "make it ",
    "increase ",
    "decrease ",
    "prefer ",
    "go with ",
    "let's use",
    "lets use",
    "please use",
    "update the",
    "rewrite",
    "revise",
)

_ACKNOWLEDGEMENTS = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "got it",
    "cool",
    "lgtm",
    "sounds good",
    "looks good",
    "nice",
    "great",
    "perfect",
    "understood",
}

_QUESTION_MARKERS = (
    "?",
    "what ",
    "what's ",
    "whats ",
    "why ",
    "how ",
    "show ",
    "list ",
    "explain ",
    "which ",
    "where ",
    "who ",
    "remind",
    "summarize",
    "summary",
    "tell me",
    "walk me",
    "can you show",
    "could you show",
    "please show",
    "please list",
    "please explain",
    "what did we",
    "what do we",
    "we agree",
    "agreed on",
)

_CONCERN_HINTS = (
    "why no",
    "why not",
    "why don't",
    "why doesnt",
    "why doesn't",
    "is there no",
    "are there no",
    "aren't there",
    "without ",
    "missing",
    "worried",
    "concern",
    "should we",
    "shouldn't",
    "too few",
    "too many",
    "not enough",
    "insecure",
    "what about",
    "you forgot",
    "please address",
    "i think",
    "i'd rather",
    "consider ",
    "doesn't",
    "does not",
    "won't work",
    "we need",
    "we should",
    "can we add",
    "can we use",
    "gap",
)

_ENDPOINT_ASK_HINTS = ("endpoint", "url", "uri", "route", "path")
_SHOW_HINTS = ("show", "list", "all", "agreed", "current", "what", "which", "remind", "we agree")

_FULL_PHASE_HINTS = (
    "entity relationship",
    "related entit",
    "remap the",
    "re-map",
    "remap entit",
    "redo the relationship",
    "rewrite the relationship",
    "tech stack",
    "change the stack",
    "switch the stack",
    "switch stack",
    "new stack",
    "all phases",
    "all working phases",
    "full update",
    "full re-interview",
    "start over",
    "from scratch",
    "re-interview",
    "walk all phases",
    "working phases",
)

FULL_PHASE_REFUSAL = (
    "A full update of this core microservice — entity relationships, features, and "
    "stack, walking every planning phase — is only available after the architect "
    "ships a new design package. You can still add or update features and bugs "
    "here and ship a new spec version to the engineer now."
)

UPDATES_HEADER = "**Updates to this proposal**"
NEXT_PROMPT_HEADER = "**What you can do next**"
_DONE_MESSAGE_RE = re.compile(
    r"(?i)^\s*session (marked )?done\.?\s*$|^\s*session ended\.?\s*$"
)

FEEDBACK_RESOLUTION_RULES = (
    "The user commented after you last asked them to Approve. You MUST:\n"
    "1. Address every query, concern, and comment in assistant_message (do not skip).\n"
    "2. Apply requested changes to this step's primary artifact; keep prior content otherwise.\n"
    "3. Never quote or restate the user's message. No 'I heard you: …' or 'Noted: …'.\n"
    "4. End assistant_message with:\n"
    f"{UPDATES_HEADER}\n"
    "- one bullet per change, in your own words\n"
    "  (or a single bullet: None — with a one-line reason if the artifact is unchanged).\n"
    "5. Then ask them to confirm, approve, or agree for THIS updated version, "
    "not the previous one. Never tell them to click a button."
)

ASK_TO_CONFIRM_RULES = (
    "When the step is ready, ask them to confirm, approve, or agree so you can "
    "continue. Never tell them to click a button or name a UI control. They may "
    "confirm in chat or use the UI — their choice."
)

ASK_TO_CONFIRM_LINE = (
    "If this looks right, confirm, approve, or agree so we can continue."
)

_CLICK_UI_RE = re.compile(
    r"(?i)\bclick(?:\s+on)?\s+(?:the\s+)?"
    r"(?:"
    r"\*\*[^*]{1,80}\*\*"
    r"|(?:Approve|Confirm|Continue|Pause|Execute|Next)(?:\s+\w+){0,8}"
    r"|(?:approve|confirm|continue|pause|execute)(?:\s+\w+){0,8}\s+button"
    r"|button"
    r")"
)


def without_click_instruction(message: str) -> str:
    """Chat must ask to confirm/approve/agree, never to click a UI control."""
    body = (message or "").strip()
    if not body:
        return body
    cleaned = _CLICK_UI_RE.sub("confirm, approve, or agree", body)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


SUGGESTED_ANSWER_RULES = (
    "If this turn is the user asking for help answering questions YOU asked — "
    "candidate options, a recommended pick, or a draft reply — that request IS "
    "the work of this turn. Judge that from the meaning of the message, not from "
    "any fixed wording.\n"
    "For every open question in your last assistant message, give 2–3 concrete "
    "potential answers and mark one (Recommended).\n"
    "Do not treat their request as their answer. Do not skip to a new question. "
    "Do not rewrite artifacts. Stay on the same questions so they can pick or edit."
)


_TURN_INTENT_SYSTEM = (
    "You are the USER TURN INTENT CLASSIFIER for a software-factory agent.\n"
    "Read the user message as a human would. Do not decide from isolated keywords.\n"
    "Classify into exactly one category:\n"
    "- command: they want the agent to ACT in the workflow (approve/advance a step, "
    "apply a design change, pause or execute work).\n"
    "- information: they want an explanation, list, reminder, comparison, or Q&A. "
    "Do not change artifacts and do not advance the workflow.\n"
    "If category is command, set action to one of:\n"
    "- approve: accept the current step and move on (Approve, looks good, next step, "
    "I'm happy with this, ship it, proceed, wrap up, continue to the next phase).\n"
    "- revise: change the spec, design, or plan from their comment or concern.\n"
    "- pause: pause in-flight execution.\n"
    "- execute: start or resume an execution plan.\n"
    "- none: some other command.\n"
    "If category is information, action must be answer.\n"
    "A question about whether/why they should approve something is information.\n"
    "If they want help answering questions you asked — candidate options, a "
    "recommended pick, or a draft reply — that is information. It is not a "
    "revision and not their interview answer. Decide from intent, not wording.\n"
    "A concern that implies a missing feature (e.g. 'why is there no rate limiting?') "
    "is command/revise.\n"
    "Respond ONLY with JSON:\n"
    '{"category":"command|information","action":"approve|revise|pause|execute|answer|none"}'
)
_VALID_CATEGORIES = frozenset({"command", "information"})
_VALID_ACTIONS = frozenset({"approve", "revise", "pause", "execute", "answer", "none"})


def _compact_user_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).rstrip(".!")


def _heuristic_approval(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or "?" in raw:
        return False
    compact = _compact_user_text(raw).lower()
    if not compact or any(hint in compact for hint in _REVISION_HINTS):
        return False
    if _ADVANCE_RE.search(compact):
        return True
    if any(hint in compact for hint in _CONCERN_HINTS):
        return False
    if compact in _APPROVAL_EXACT:
        return True
    return bool(_APPROVAL_PREFIX_RE.match(compact) or _APPROVAL_DIRECT_RE.match(compact))


def _heuristic_turn_intent(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    compact = _compact_user_text(raw).lower()
    if not compact:
        return "information", "none"
    if "?" in raw and any(hint in compact for hint in _CONCERN_HINTS):
        return "command", "revise"
    if _heuristic_approval(raw):
        return "command", "approve"
    if any(hint in compact for hint in _REVISION_HINTS) or any(
        hint in compact for hint in _CONCERN_HINTS
    ):
        return "command", "revise"
    if compact in _ACKNOWLEDGEMENTS:
        return ("command", "approve") if compact in _APPROVAL_EXACT else ("information", "answer")
    if any(marker in compact for marker in _QUESTION_MARKERS) or "?" in raw:
        return "information", "answer"
    return "command", "revise"


def _llm_turn_intent(text: str, context: str) -> tuple[str, str] | None:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from orchestrator_agent.json_util import parse_llm_json_object
        from orchestrator_agent.llm import get_chat_model

        model = get_chat_model()
        response = model.invoke(
            [
                SystemMessage(content=_TURN_INTENT_SYSTEM),
                HumanMessage(
                    content=(
                        f"Workflow context:\n{context or '(none)'}\n\n"
                        f"User message:\n{text}\n"
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
        parsed = parse_llm_json_object(str(content))
        category = str(parsed.get("category") or "").strip().lower()
        action = str(parsed.get("action") or "").strip().lower()
        if category not in _VALID_CATEGORIES:
            return None
        if category == "information":
            return "information", "answer"
        if action not in _VALID_ACTIONS or action == "answer":
            action = "none"
        return "command", action
    except Exception as exc:
        logger.warning("Turn intent classifier failed; using fallback: %s", exc)
        return None


@lru_cache(maxsize=256)
def classify_user_message(text: str, context: str = "") -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "information", "none"
    classified = _llm_turn_intent(raw, context)
    if classified is not None:
        return classified
    return _heuristic_turn_intent(raw)


def needs_artifact_update(text: str) -> bool:
    """True when comments/concerns should rewrite the current proposal before Approve."""
    if not (text or "").strip():
        return False
    return classify_user_message(text) == ("command", "revise")


def is_informational_query(text: str) -> bool:
    """True when chat should be answered from current artifacts, not used to rewrite them."""
    t = (text or "").strip()
    if not t:
        return False
    return classify_user_message(t)[0] == "information"


def is_revision_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return classify_user_message(t) == ("command", "revise")


def is_full_phase_request(text: str) -> bool:
    """True when the user wants to re-walk relations/features/stack, not a spec delta."""
    compact = _compact_user_text(text).lower()
    if not compact:
        return False
    if any(hint in compact for hint in _FULL_PHASE_HINTS):
        return True
    if re.search(
        r"\b(switch|change|move)\b.+\b(to )?(java|python|go|golang|rust|kotlin|node|typescript)\b",
        compact,
    ) and any(token in compact for token in ("stack", "language", "framework", "runtime")):
        return True
    return False


def wants_endpoint_list(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _ENDPOINT_ASK_HINTS) and any(h in t for h in _SHOW_HINTS)


_APPROVAL_EXACT = {
    "approve",
    "approved",
    "i approve",
    "i approve this",
    "i approve it",
    "approve this",
    "approve it",
    "approve this version",
    "approve the plan",
    "approve the design",
    "approve the spec",
    "approve the features",
    "approve features",
    "approve the stack",
    "please approve",
    "yes, approve",
    "yes approve",
    "ok, approve",
    "okay, approve",
    "lgtm",
    "looks good",
    "looks great",
    "looks right",
    "looks correct",
    "sounds good",
    "sounds great",
    "that's good",
    "thats good",
    "that's fine",
    "thats fine",
    "good to go",
    "ship it",
    "go ahead",
    "please go ahead",
    "proceed",
    "let's proceed",
    "lets proceed",
    "let's go",
    "lets go",
    "i accept",
    "accept",
    "lock it in",
    "ready to approve",
}

_APPROVAL_PREFIX_RE = re.compile(
    r"^(?:yes|yep|yeah|ok|okay|please)[,.]?\s+"
    r"(?:i\s+)?(?:would like to\s+)?(?:approve|proceed|go ahead)\b",
    re.I,
)
_APPROVAL_DIRECT_RE = re.compile(
    r"^(?:(?:yes|yep|yeah|ok|okay|please)[,.]?\s+)?"
    r"(?:i(?:'m| am)?\s+)?(?:would like to\s+|want to\s+)?"
    r"(?:happy to\s+|ready to\s+)?(?:approve|accept)\b",
    re.I,
)
_ADVANCE_RE = re.compile(
    r"(?i)(?:"
    r"\bnext(?:\s+the)?\s+step\b|"
    r"\bnext\s+phase\b|"
    r"\bmove\s+on\b|"
    r"\bmove\s+(?:ahead|forward|to\s+the\s+next)\b|"
    r"\bgo\s+(?:on\s+to|to\s+the\s+next)\b|"
    r"\bcontinue\s+(?:on|to\s+the\s+next)\b|"
    r"\bwrap(?:ping)?\s+(?:it|this|the\s+step)?\s*up\b|"
    r"\bwrap\s+up\b|"
    r"\bthat'?s\s+(?:enough|all)\b|"
    r"\benough\s+for\s+(?:this|now|the\s+step)\b|"
    r"\bdone\s+with\s+this\s+step\b|"
    r"\bfinish\s+(?:this|the)\s+step\b|"
    r"\bskip\s+(?:this\s+step|to\s+the\s+next)\b|"
    r"\blet'?s\s+continue\b|"
    r"\bonward\b"
    r")"
)


def is_advance_request(text: str) -> bool:
    """True when chat asks to wrap up this step and go to the next one immediately."""
    raw = (text or "").strip()
    if not raw:
        return False
    category, action = classify_user_message(raw)
    if category == "command" and action == "approve":
        return bool(_ADVANCE_RE.search(_compact_user_text(raw).lower()))
    return False


def is_step_approval_message(text: str) -> bool:
    """True when chat is a command to accept the current step and move on."""
    raw = (text or "").strip()
    if not raw:
        return False
    return classify_user_message(raw) == ("command", "approve")


def promote_chat_to_approve(action: str, text: str, *, can_approve: bool = True) -> str:
    del can_approve
    if action != "chat":
        return action
    if is_step_approval_message(text):
        return "approve"
    return action


def format_next_prompt(
    *,
    approve_label: str = "",
    can_approve: bool = True,
    mode: str = "step",
) -> str:
    """User-visible footer: what to do next to continue this process."""
    del approve_label
    lines = [NEXT_PROMPT_HEADER]
    if mode == "handoff":
        lines.extend(
            [
                "- Continue on this tile if you want to revise, then hand off an updated plan.",
                "- Open another microservice tile to keep planning.",
            ]
        )
    elif mode == "idle":
        lines.extend(
            [
                "- Open a microservice tile to continue planning that service.",
                "- Or wait for the next architect package.",
            ]
        )
    elif can_approve:
        lines.extend(
            [
                "- Confirm, approve, or agree (in chat or the UI) to continue this process.",
                "- Ask a question about this step.",
                "- Tell me what to change before we move on.",
            ]
        )
    else:
        lines.extend(
            [
                "- Reply with more detail so we can complete this step.",
                "- Or say `next step` / `wrap up` to move on with what we have.",
                "- Ask a question about the current proposal.",
            ]
        )
    return "\n".join(lines)


def with_next_prompt(
    message: str,
    *,
    approve_label: str = "",
    can_approve: bool = True,
    mode: str = "step",
) -> str:
    """Return the reply without a 'What you can do next' footer."""
    del approve_label, can_approve, mode
    body = without_click_instruction((message or "").strip())
    if not body or _DONE_MESSAGE_RE.search(body):
        return body
    return re.split(r"(?i)\n*\*\*What you can do next\*\*", body, maxsplit=1)[0].strip()


def with_resolution_close(
    message: str,
    *,
    changed: bool,
    change_lines: list[str] | None = None,
    approve_label: str = "",
    can_approve: bool = True,
) -> str:
    """Ensure a changelog and a next-action prompt after resolving user comments."""
    body = (message or "").strip()
    if UPDATES_HEADER.lower() not in body.lower():
        if changed:
            lines = change_lines or [
                "Applied your latest comments to this step's proposal (see the artifact)."
            ]
        else:
            lines = [
                "None — the current proposal is unchanged since the last approval request."
            ]
        section = UPDATES_HEADER + "\n" + "\n".join(
            f"- {line.lstrip('- ').strip()}" for line in lines if str(line).strip()
        )
        body = f"{body}\n\n{section}".strip()
    return with_next_prompt(body, approve_label=approve_label, can_approve=can_approve)
