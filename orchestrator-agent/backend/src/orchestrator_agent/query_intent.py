"""Detect informational Q&A vs artifact-revision chat before Approve."""

from __future__ import annotations

import re

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

UPDATES_HEADER = "**Updates to this proposal**"
_APPROVE_AGAIN = "Approve this version if it looks right, or tell me what else to change."

FEEDBACK_RESOLUTION_RULES = (
    "The user commented after you last asked them to Approve. You MUST:\n"
    "1. Address every query, concern, and comment in assistant_message (do not skip).\n"
    "2. Apply requested changes to this step's primary artifact; keep prior content otherwise.\n"
    "3. End assistant_message with:\n"
    f"{UPDATES_HEADER}\n"
    "- one bullet per change, tied to their comment\n"
    "  (or a single bullet: None — with a one-line reason if the artifact is unchanged).\n"
    "4. Then invite Approve for THIS updated version, not the previous one."
)


def needs_artifact_update(text: str) -> bool:
    """True when comments/concerns should rewrite the current proposal before Approve."""
    t = (text or "").lower().strip()
    if not t:
        return False
    if any(hint in t for hint in _REVISION_HINTS):
        return True
    if any(hint in t for hint in _CONCERN_HINTS):
        return True
    compact = t.rstrip(".!")
    if compact in _ACKNOWLEDGEMENTS:
        return False
    if any(marker in t for marker in _QUESTION_MARKERS):
        return False
    return True


def is_informational_query(text: str) -> bool:
    """True when chat should be answered from current artifacts, not used to rewrite them."""
    t = (text or "").strip()
    if not t:
        return False
    return not needs_artifact_update(t)


def is_revision_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return needs_artifact_update(t)


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
    r"^(?:i(?:'m| am) )?(?:happy to |ready to )?approve\b",
    re.I,
)


def is_step_approval_message(text: str) -> bool:
    """True when chat is an approval to advance the current step (same as the Approve button)."""
    raw = (text or "").strip()
    if not raw or "?" in raw:
        return False
    compact = re.sub(r"\s+", " ", raw.lower()).rstrip(".!")
    if not compact:
        return False
    if any(hint in compact for hint in _REVISION_HINTS):
        return False
    if any(hint in compact for hint in _CONCERN_HINTS):
        return False
    if compact in _APPROVAL_EXACT:
        return True
    return bool(_APPROVAL_PREFIX_RE.match(compact) or _APPROVAL_DIRECT_RE.match(compact))


def promote_chat_to_approve(action: str, text: str, *, can_approve: bool) -> str:
    if action == "chat" and can_approve and is_step_approval_message(text):
        return "approve"
    return action


def with_resolution_close(
    message: str,
    *,
    changed: bool,
    change_lines: list[str] | None = None,
) -> str:
    """Ensure a changelog and a fresh Approve ask after resolving user comments."""
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
    tail = body.lower()[-500:]
    if "approve this version" not in tail and not re.search(
        r"(?i)\b(click approve|invite approve|approve when|approve to |approve this)\b",
        tail,
    ):
        body = f"{body}\n\n{_APPROVE_AGAIN}"
    return body
