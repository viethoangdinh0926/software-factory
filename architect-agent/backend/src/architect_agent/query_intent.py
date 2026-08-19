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

_HTTP_ENDPOINT_RE = re.compile(
    r"(?im)\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(`?/+[A-Za-z0-9_{}/.\-:]*)"
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
    "3. End assistant_message with:\n"
    f"{UPDATES_HEADER}\n"
    "- one bullet per change, tied to their comment\n"
    "  (or a single bullet: None — with a one-line reason if the artifact is unchanged).\n"
    "4. Then invite Approve for THIS updated version, not the previous one. "
    "A **What you can do next** prompt is appended by the system."
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


def format_next_prompt(
    *,
    approve_label: str = "",
    can_approve: bool = True,
    mode: str = "step",
) -> str:
    """User-visible footer: what to do next to continue this process."""
    label = (approve_label or "Approve").strip() or "Approve"
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
                f"- Click **{label}**, or say `Approve` / `next step` / `looks good`, to continue this process.",
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
    """Append a next-action prompt unless the message already has one or is a session-end note."""
    body = (message or "").strip()
    if not body or _DONE_MESSAGE_RE.search(body):
        return body
    if NEXT_PROMPT_HEADER.lower() in body.lower():
        return body
    return (
        f"{body}\n\n"
        f"{format_next_prompt(approve_label=approve_label, can_approve=can_approve, mode=mode)}"
    )


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
    if not raw or "?" in raw:
        return False
    compact = re.sub(r"\s+", " ", raw.lower()).rstrip(".!")
    if not compact:
        return False
    if any(hint in compact for hint in _REVISION_HINTS):
        return False
    return bool(_ADVANCE_RE.search(compact))


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
    if is_advance_request(raw):
        return True
    if any(hint in compact for hint in _CONCERN_HINTS):
        return False
    if compact in _APPROVAL_EXACT:
        return True
    return bool(_APPROVAL_PREFIX_RE.match(compact) or _APPROVAL_DIRECT_RE.match(compact))


def promote_chat_to_approve(action: str, text: str, *, can_approve: bool) -> str:
    if action != "chat":
        return action
    if is_advance_request(text):
        return "approve"
    if can_approve and is_step_approval_message(text):
        return "approve"
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
