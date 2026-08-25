"""Understand what the user wants this turn (approve, revise, ask) — not keyword-only."""

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
    "stand-alone",
    "standalone",
    "self-contained",
    "self contained",
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

_HTTP_ENDPOINT_RE = re.compile(
    r"(?im)\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(`?/+[A-Za-z0-9_{}/.\-:]*)"
)

UPDATES_HEADER = "**Updates to this proposal**"
NEXT_PROMPT_HEADER = "**What you can do next**"
_DONE_MESSAGE_RE = re.compile(
    r"(?i)^\s*session (marked )?done\.?\s*$|^\s*session ended\.?\s*$"
)

USER_MESSAGE_FIRST_RULES = (
    "USER MESSAGE FIRST (non-negotiable):\n"
    "The labeled Latest user message is the work of this turn. Discussion memory "
    "and the recent transcript are context, not a reason to ignore that message. "
    "You MUST:\n"
    "1. Address every question, concern, objection, preference, confirmation, and "
    "comment in assistant_message. Do not skip any of them.\n"
    "2. Do NOT reply with only the next prepared interview question or the next "
    "step script. A canned next-question with no response to what they just said "
    "is a FAILED turn.\n"
    "2b. If they asked for help answering questions YOU asked (candidate options, "
    "a recommended pick, or a draft reply): give 2–3 concrete options per open "
    "question and mark one (Recommended). Do not treat that request as their "
    "answer. Do not skip to a new question.\n"
    "3. If they asked to change the spec or design, apply it this turn.\n"
    "4. Only AFTER addressing them may you ask at most ONE follow-up, and only if "
    "it is still needed.\n"
    "5. Never quote, restate, or prefix with the user's own words. Do not write "
    "'I heard you: …', 'Noted: …', 'I applied your comments (…)', or paste their "
    "message back. Address the substance only.\n"
    "6. Do not add an **Updates to this proposal** section.\n"
    "7. Do not start a new interview or re-introduce the process as if this were "
    "the first turn. Do not stall with 'I need more information to proceed' or "
    "'tell me more about your system'. Reply as a continuation of this conversation.\n"
)


def user_message_first_block(pending: str) -> str:
    """System-prompt fragment: only when the user actually said something this turn."""
    if not (pending or "").strip():
        return ""
    return f"{USER_MESSAGE_FIRST_RULES}\n"

ASK_TO_CONFIRM_RULES = (
    "When the step is ready, ask them to confirm, approve, or agree so you can "
    "continue. Never tell them to click a button or name a UI control. They may "
    "confirm in chat or use the UI — their choice."
)

ASK_TO_CONFIRM_LINE = (
    "If this looks right, confirm, approve, or agree so we can continue."
)

FEEDBACK_RESOLUTION_RULES = (
    "The user commented on the current proposal. You MUST follow USER MESSAGE FIRST.\n"
    f"{USER_MESSAGE_FIRST_RULES}\n"
    "Then ask them to confirm, approve, or agree for THIS updated version, not the "
    "previous one. Never tell them to click a button."
)

SUGGESTED_ANSWER_RULES = (
    "If this turn is the user asking for help answering questions YOU asked — "
    "candidate options, a recommended pick, or a draft reply — that request IS "
    "the work of this turn. Judge that from the meaning of the message, not from "
    "any fixed wording.\n"
    "For every open question in your last assistant message and any unanswered "
    "interview checklist items you already posed, give 2–3 concrete potential "
    "answers and mark one (Recommended).\n"
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
    "- revise: change the spec, design, or plan. This includes accepting a "
    "recommended interview option so the agent writes that pick into the living "
    "spec. It is NOT approve — do not advance the track.\n"
    "- pause: pause in-flight execution.\n"
    "- execute: start or resume an execution plan.\n"
    "- none: some other command.\n"
    "If the last assistant offered a (Recommended) option or a labeled "
    "Recommendation, and they accept that pick (however they phrase it) without "
    "asking to move to the next step, classify command/revise. The agent will "
    "copy the recommended text into the spec.\n"
    "If category is information, action must be answer.\n"
    "A question about whether/why they should approve something is information.\n"
    "If they want help answering questions you asked — candidate options, a "
    "recommended pick, or a draft reply — that is information. It is not a "
    "revision and not their interview answer. Decide from intent, not wording.\n"
    "A concern that implies a missing feature (e.g. 'why is there no rate limiting?') "
    "is command/revise.\n"
    "This classification is the instruction the agent will follow for this turn: "
    "approve advances, revise writes their change, answer is Q&A only.\n"
    "Respond ONLY with JSON:\n"
    '{"category":"command|information","action":"approve|revise|pause|execute|answer|none"}'
)

_VALID_CATEGORIES = frozenset({"command", "information"})
_VALID_ACTIONS = frozenset({"approve", "revise", "pause", "execute", "answer", "none"})


def _compact_user_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).rstrip(".!")


def _heuristic_turn_intent(text: str) -> tuple[str, str]:
    """Fallback only when the classifier LLM is unavailable or returns invalid JSON."""
    raw = (text or "").strip()
    compact = _compact_user_text(raw).lower()
    if not compact:
        return "information", "none"
    if re.search(r"\b(weather|asdf|qwerty|lorem ipsum)\b", compact):
        return "information", "answer"
    if looks_like_help_answering(raw):
        return "information", "answer"
    if re.search(r"\bskip (this|that|the current) question\b", compact):
        return "command", "revise"
    if "that's all i have" in compact or "thats all i have" in compact:
        return "command", "approve"
    if "?" in raw and any(hint in compact for hint in _CONCERN_HINTS):
        return "command", "revise"
    if _heuristic_approval(raw):
        return "command", "approve"
    if _heuristic_accept_recommendation(raw):
        return "command", "revise"
    if any(hint in compact for hint in _REVISION_HINTS) or any(
        hint in compact for hint in _CONCERN_HINTS
    ):
        return "command", "revise"
    if compact in _ACKNOWLEDGEMENTS:
        if compact in _APPROVAL_EXACT:
            return "command", "approve"
        return "information", "answer"
    if any(marker in compact for marker in _QUESTION_MARKERS) or "?" in raw:
        return "information", "answer"
    return "command", "revise"


def is_accept_recommendation_message(text: str, context: str = "") -> bool:
    """True when this turn accepts a recommended pick (LLM classify, heuristic fallback)."""
    raw = (text or "").strip()
    if not raw:
        return False
    if classify_user_message(raw, context) != ("command", "revise"):
        return False
    return _heuristic_accept_recommendation(raw)


def _heuristic_accept_recommendation(text: str) -> bool:
    """Fallback only: they accepted a recommended pick, not a step advance."""
    compact = _compact_user_text(text).lower()
    if not compact or "?" in (text or ""):
        return False
    if _heuristic_approval(text):
        return False
    return bool(
        re.search(
            r"(?i)("
            r"as you recommended|as recommended|your recommendation|"
            r"go with (?:your|the) recommend|the first (?:one|option)|option\s*1"
            r")",
            compact,
        )
    )


def _heuristic_approval(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or "?" in raw:
        return False
    compact = _compact_user_text(raw).lower()
    if not compact:
        return False
    if any(hint in compact for hint in _REVISION_HINTS):
        return False
    if _ADVANCE_RE.search(compact):
        return True
    if any(hint in compact for hint in _CONCERN_HINTS):
        return False
    if compact in _APPROVAL_EXACT:
        return True
    return bool(_APPROVAL_PREFIX_RE.match(compact) or _APPROVAL_DIRECT_RE.match(compact))


def format_classify_context(workflow: str = "", last_assistant: str = "") -> str:
    """Pack workflow + last agent turn so every classify call sees both sides."""
    return (
        f"{(workflow or '').strip()}\n\n"
        f"Last assistant message:\n{(last_assistant or '').strip() or '(none)'}"
    ).strip()


def _split_classify_context(context: str) -> tuple[str, str]:
    ctx = (context or "").strip()
    marker = "Last assistant message:"
    if marker in ctx:
        workflow, _, last = ctx.partition(marker)
        return workflow.strip() or "(none)", last.strip() or "(none)"
    # Bare context is the last assistant turn (wait nodes pass it that way).
    return "(none)", ctx or "(none)"


def _llm_turn_intent(text: str, context: str) -> tuple[str, str] | None:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from architect_agent.json_util import parse_llm_json_object
        from architect_agent.llm import get_chat_model

        workflow, last_assistant = _split_classify_context(context)
        model = get_chat_model()
        response = model.invoke(
            [
                SystemMessage(content=_TURN_INTENT_SYSTEM),
                HumanMessage(
                    content=(
                        "Classify this turn using BOTH messages.\n\n"
                        f"Last assistant message:\n{last_assistant}\n\n"
                        f"Latest user message:\n{text}\n\n"
                        f"Workflow context:\n{workflow}\n"
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


_HELP_ANSWER_RE = re.compile(
    r"(?is)("
    r"suggest (?:a |an |some )?(?:response|reply|answer|option)"
    r"|help me answer"
    r"|draft (?:a |some |me )?(?:repl|answer|response)"
    r"|potential answers?"
    r"|example answers?"
    r"|what would you (?:pick|suggest|recommend)"
    r"|give me (?:some |a )?(?:potential |example |suggested )?(?:answers?|options|replies)"
    r"|can you suggest"
    r")"
)


def looks_like_help_answering(text: str) -> bool:
    """True when they want candidate answers to questions we asked."""
    body = (text or "").strip()
    if not body:
        return False
    return bool(_HELP_ANSWER_RE.search(body))


@lru_cache(maxsize=256)
def classify_user_message(text: str, context: str = "") -> tuple[str, str]:
    """Classify a user turn as (category, action).

    category is ``command`` (agent must act) or ``information`` (answer only).
    """
    raw = (text or "").strip()
    if not raw:
        return "information", "none"
    classified = _llm_turn_intent(raw, context)
    if classified is not None:
        return classified
    return _heuristic_turn_intent(raw)


def needs_artifact_update(text: str, context: str = "") -> bool:
    """True when comments/concerns should rewrite the current proposal before Approve."""
    if not (text or "").strip():
        return False
    return classify_user_message(text, context) == ("command", "revise")


def is_informational_query(text: str, context: str = "") -> bool:
    """True when chat should be answered from current artifacts, not used to rewrite them."""
    t = (text or "").strip()
    if not t:
        return False
    return classify_user_message(t, context)[0] == "information"


def is_revision_request(text: str, context: str = "") -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return classify_user_message(t, context) == ("command", "revise")


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
                "- Continue in the Orchestrator session to plan implementation.",
                "- If handoff failed, retry this same package from here.",
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
                f"- Confirm, approve, or agree (in chat or the UI) to continue this process.",
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


_ECHO_PREFIX_RE = re.compile(
    r"(?is)^\s*(?:"
    r"I applied your (?:latest )?comments|"
    r"I heard you|"
    r"I addressed your comment|"
    r"Addressed your comment|"
    r"Noted"
    r")\s*[:\(]\s*.{0,240}?(?:\)[.!]?\s*|[.!]+\s+)"
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


def without_user_echo(message: str, user_text: str = "") -> str:
    """Drop a leading 'I heard you: …' restatement — never punch holes in the reply."""
    del user_text
    body = (message or "").strip()
    if not body:
        return body
    return without_click_instruction(_ECHO_PREFIX_RE.sub("", body).strip())


def with_next_prompt(
    message: str,
    *,
    approve_label: str = "",
    can_approve: bool = True,
    mode: str = "step",
) -> str:
    """Return the reply without a 'What you can do next' footer."""
    del approve_label, can_approve, mode
    body = without_user_echo((message or "").strip())
    if not body or _DONE_MESSAGE_RE.search(body):
        return body
    body = re.split(r"(?i)\n*\*\*Updates to this proposal\*\*", body, maxsplit=1)[0].strip()
    return re.split(r"(?i)\n*\*\*What you can do next\*\*", body, maxsplit=1)[0].strip()


def with_resolution_close(
    message: str,
    *,
    changed: bool,
    change_lines: list[str] | None = None,
    approve_label: str = "",
    can_approve: bool = True,
) -> str:
    """Sanitize the reply after resolving user comments — no changelog footer."""
    del changed, change_lines
    return with_next_prompt(message, approve_label=approve_label, can_approve=can_approve)


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


def is_advance_request(text: str, context: str = "") -> bool:
    """True when chat asks to wrap up this step and go to the next one immediately."""
    raw = (text or "").strip()
    if not raw:
        return False
    category, action = classify_user_message(raw, context)
    if category == "command" and action == "approve":
        compact = _compact_user_text(raw).lower()
        return bool(_ADVANCE_RE.search(compact))
    return False


def is_step_approval_message(text: str, context: str = "") -> bool:
    """True when chat is a command to accept the current step and move on."""
    raw = (text or "").strip()
    if not raw:
        return False
    return classify_user_message(raw, context) == ("command", "approve")


def promote_chat_to_approve(
    action: str, text: str, *, can_approve: bool = True, context: str = ""
) -> str:
    """Promote chat to approve when the user issued an approve/advance command.

    ``can_approve`` is ignored: the user's command is authoritative even if the
    model forgot to set ready_to_advance.
    """
    del can_approve
    if action != "chat":
        return action
    if is_step_approval_message(text, context):
        return "approve"
    return action


def workflow_action(action: str) -> str:
    """Map classify action onto the resume action the graph understands."""
    mapped = (action or "").strip().lower()
    if mapped in {"approve", "revise", "answer", "pause", "execute"}:
        return mapped
    return "chat"


def resolve_wait_action(
    resume_action: str,
    user_text: str,
    context: str = "",
    *,
    consult_kind: str = "",
) -> str:
    """Honor the chat-entry classify; only re-classify leftover ``chat``."""
    action = (resume_action or "chat").strip().lower()
    if action in {"pause", "execute"}:
        action = "chat"
    if action in {"approve", "revise", "answer", "session_done"}:
        return action
    action = promote_chat_to_approve("chat", user_text, context=context)
    if consult_kind == "approve" and action == "chat":
        return "approve"
    if action == "chat" and user_text and is_informational_query(user_text, context):
        return "answer"
    return action


def extract_http_endpoints(*texts: str) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    found: set[tuple[str, str]] = set()
    for text in texts:
        for match in _HTTP_ENDPOINT_RE.finditer(text or ""):
            method = match.group(1).upper()
            path = match.group(2).strip().strip("`")
            if not path.startswith("/"):
                path = "/" + path
            item = (method, path)
            if item not in found:
                found.add(item)
                seen.append(item)
    return seen


def format_agreed_endpoints(endpoints: list[tuple[str, str]]) -> str:
    if not endpoints:
        return (
            "No URL endpoints are recorded yet. The architect agrees core microservices "
            "and communication schemes; HTTP/gRPC path specs are completed later with "
            "the orchestrator. Ask about service ownership or protocols, or Approve "
            "once this step's artifact is complete."
        )
    lines = ["URL endpoints currently recorded in this design:", ""]
    lines.extend(f"- `{method} {path}`" for method, path in endpoints)
    return "\n".join(lines)
