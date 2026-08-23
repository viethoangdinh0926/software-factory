from __future__ import annotations

import re
from typing import Any, Iterable

_QUESTION_TITLE_RE = re.compile(r"❓\s*\*\*([^*]+)\*\*", re.MULTILINE)
_BOLD_TITLE_RE = re.compile(r"^\*\*([^*]+)\*\*\s*:", re.MULTILINE)

# Ordered discovery checklist. Ask the first topic that is still thin in the living spec
# and has not already been asked (by title / keyword overlap).
CHECKLIST: tuple[dict[str, Any], ...] = (
    {
        "id": "problem",
        "title": "Problem / opportunity",
        "section_hints": ("## problem", "## opportunity"),
        "keywords": ("problem", "opportunity", "pain", "why build"),
        "question": (
            "❓ **Problem / opportunity**: What concrete pain or opportunity makes this "
            "worth building now?\n\n"
            "➡️ (Recommended) One paragraph naming who hurts today and what fails without this system."
        ),
    },
    {
        "id": "actors",
        "title": "Primary actors",
        "section_hints": ("## actors", "## users"),
        "keywords": ("actor", "user", "role", "job", "who uses"),
        "question": (
            "❓ **Primary actors**: Who uses this system day-to-day, and what job are they "
            "hiring it to do?\n\n"
            "➡️ (Recommended) Name 1–2 concrete roles with a single primary job each."
        ),
    },
    {
        "id": "scope",
        "title": "V1 scope",
        "section_hints": ("## in scope", "## goals", "## in-scope"),
        "keywords": ("scope", "v1", "in scope", "capability", "mvp"),
        "question": (
            "❓ **V1 scope**: What 3 capabilities must ship in v1 for the primary job to succeed?\n\n"
            "➡️ (Recommended) List three user-visible capabilities, not infrastructure."
        ),
    },
    {
        "id": "nongoals",
        "title": "Out of scope",
        "section_hints": ("## out of scope", "## non-goals", "## non goals"),
        "keywords": ("out of scope", "non-goal", "nongoal", "not in v1"),
        "question": (
            "❓ **Out of scope / non-goals**: What will you explicitly NOT build in v1?\n\n"
            "➡️ (Recommended) Name 2–3 tempting features that are deferred on purpose."
        ),
    },
    {
        "id": "invariants",
        "title": "Critical invariants",
        "section_hints": ("## critical invariant", "## invariants"),
        "keywords": ("invariant", "must never", "safety", "compliance", "trust"),
        "question": (
            "❓ **Critical invariants**: What must never go wrong (money, safety, compliance, trust)?\n\n"
            "➡️ (Recommended) List the top 1–3 invariants in plain language."
        ),
    },
    {
        "id": "success",
        "title": "Success criteria",
        "section_hints": ("## success", "## metrics"),
        "keywords": ("success", "metric", "kpi", "done when"),
        "question": (
            "❓ **Success criteria**: How will you know v1 worked for the primary actors?\n\n"
            "➡️ (Recommended) 2–3 observable outcomes (behavior or metrics), not vanity stats."
        ),
    },
    {
        "id": "assumptions",
        "title": "Assumptions & risks",
        "section_hints": ("## assumption", "## risk"),
        "keywords": ("assumption", "risk", "unknown", "open question"),
        "question": (
            "❓ **Assumptions & risks**: What are you assuming that is not yet written down, "
            "and what is the biggest risk if wrong?\n\n"
            "➡️ (Recommended) Name 1–2 assumptions and the failure mode for each."
        ),
    },
)


def extract_question_titles(messages: Iterable[dict[str, Any]]) -> list[str]:
    """Collect prior interview question titles from assistant turns."""
    titles: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "")
        found = _QUESTION_TITLE_RE.findall(content) or _BOLD_TITLE_RE.findall(content)
        for raw in found:
            title = _normalize_topic(raw)
            if title and title not in seen:
                seen.add(title)
                titles.append(raw.strip())
    return titles


def _normalize_topic(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s/&-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _section_has_substance(spec: str, hints: tuple[str, ...]) -> bool:
    lower = spec.lower()
    for hint in hints:
        idx = lower.find(hint)
        if idx < 0:
            continue
        # Take content until next ## heading or end.
        rest = spec[idx + len(hint) :]
        nxt = re.search(r"\n##\s+", rest)
        body = rest[: nxt.start()] if nxt else rest
        cleaned = re.sub(r"[\W_]+", "", body.lower())
        # Ignore placeholder fluff.
        if len(cleaned) >= 24 and "toberefined" not in cleaned and "tbd" not in cleaned:
            return True
    return False


def _already_asked(item: dict[str, Any], asked_titles: list[str]) -> bool:
    asked_norm = [_normalize_topic(t) for t in asked_titles]
    title_norm = _normalize_topic(str(item["title"]))
    keywords = tuple(item.get("keywords") or ())
    for asked in asked_norm:
        if not asked:
            continue
        if asked == title_norm or title_norm in asked or asked in title_norm:
            return True
        if any(k in asked for k in keywords):
            return True
    return False


def uncovered_checklist(spec: str, asked_titles: list[str]) -> list[dict[str, Any]]:
    """Return checklist items still thin in the spec and not already asked."""
    open_items: list[dict[str, Any]] = []
    for item in CHECKLIST:
        if _already_asked(item, asked_titles):
            continue
        if _section_has_substance(spec, tuple(item["section_hints"])):
            continue
        open_items.append(item)
    return open_items


def question_topic_id(text: str) -> str | None:
    """Map a question title/body to a checklist topic id when possible."""
    titles = _QUESTION_TITLE_RE.findall(text) or _BOLD_TITLE_RE.findall(text)
    blob = _normalize_topic(" ".join(titles) if titles else text[:120])
    for item in CHECKLIST:
        title_norm = _normalize_topic(str(item["title"]))
        if title_norm and (title_norm in blob or blob in title_norm):
            return str(item["id"])
        if any(k in blob for k in item.get("keywords") or ()):
            return str(item["id"])
    return None


def is_repeat_question(candidate: str, asked_titles: list[str], prior_assistants: list[str]) -> bool:
    """Heuristic: same title family, same checklist topic, or high overlap with a prior question."""
    cand_topic = question_topic_id(candidate)
    for asked in asked_titles:
        if cand_topic and question_topic_id(asked) == cand_topic:
            return True
    for prev in prior_assistants:
        if cand_topic and question_topic_id(prev) == cand_topic:
            return True

    titles = _QUESTION_TITLE_RE.findall(candidate) or _BOLD_TITLE_RE.findall(candidate)
    cand_title = _normalize_topic(titles[0]) if titles else _normalize_topic(candidate[:80])
    for asked in asked_titles:
        asked_n = _normalize_topic(asked)
        if not asked_n or not cand_title:
            continue
        if cand_title == asked_n or cand_title in asked_n or asked_n in cand_title:
            return True
        a = {t for t in asked_n.split() if len(t) > 3}
        b = {t for t in cand_title.split() if len(t) > 3}
        if a and b and len(a & b) / max(1, len(a | b)) >= 0.5:
            return True

    cand_body = _normalize_topic(candidate)
    for prev in prior_assistants:
        if "❓" not in prev and "**" not in prev:
            continue
        prev_n = _normalize_topic(prev)
        if not prev_n or len(prev_n) < 40:
            continue
        if cand_body[:100] and prev_n[:100] and cand_body[:100] == prev_n[:100]:
            return True
    return False


def fallback_question(spec: str, asked_titles: list[str]) -> tuple[str, bool]:
    """
    Deterministic next question from uncovered checklist.
    Returns (assistant_message, ready_for_design).
    """
    open_items = uncovered_checklist(spec, asked_titles)
    if open_items:
        return str(open_items[0]["question"]), False
    return (
        "The readiness checklist looks covered. You can keep adding detail, "
        "or approve to move into market evaluation and system design.",
        True,
    )


_VAGUE_STALL_RE = re.compile(
    r"(?is)("
    r"need more information to proceed"
    r"|provide more details about (?:your )?(?:the )?system"
    r"|tell me more about (?:your )?(?:the )?system"
    r"|please (?:share|provide|give) more details"
    r"|please share more detail"
    r"|need a bit more detail"
    r"|describe (?:your|the) (?:system|application|app) in more detail"
    r"|what else should we lock for v1\?"
    r")"
)

def is_vague_question(text: str) -> bool:
    """True when chat stalls on a generic 'tell me more' instead of a concrete choice."""
    body = (text or "").strip()
    if not body:
        return True
    core = re.split(r"\n\*\*Updates to this proposal\*\*", body, maxsplit=1)[0]
    core = re.split(r"\n\*\*What you can do next\*\*", core, maxsplit=1)[0].strip()
    if not _VAGUE_STALL_RE.search(core):
        return False
    if "❓" in core:
        return False
    words = core.split()
    if len(words) > 80 and "?" in core:
        return False
    return True


def lock_notice_for_pending(pending: str) -> str:
    """One-sentence lock when the user confirms dropping a concern we raised."""
    p = (pending or "").strip()
    if not p:
        return ""
    lower = p.lower()
    dropping = any(
        token in lower
        for token in (
            "ignor",
            "skip",
            "don't need",
            "do not need",
            "dont need",
            "no need",
            "without",
            "not needed",
            "out of scope",
        )
    )
    if not dropping:
        return ""
    bits: list[str] = []
    if "sync" in lower:
        bits.append("no remote / cross-device synchronization in v1")
    if "secur" in lower:
        bits.append(
            "no extra credential-vault or encryption work in v1 beyond ordinary OS file access"
        )
    if not bits:
        bits.append("the constraints you confirmed dropping")
    joined = "; ".join(bits)
    return (
        f"Locked: **{joined}**. I will not keep arguing those — they are accepted "
        f"v1 constraints, recorded in the spec.\n\n"
    )


def specific_followup_message(
    spec: str,
    pending: str = "",
    asked_titles: list[str] | None = None,
) -> tuple[str, bool]:
    """Lock confirmed drop-decisions, then ask the next uncovered concrete question."""
    follow, ready = fallback_question(spec, asked_titles or [])
    notice = lock_notice_for_pending(pending)
    if notice:
        return f"{notice}{follow}", ready
    return follow, ready


def ensure_specific_question(
    text: str,
    *,
    spec: str,
    pending: str = "",
    asked_titles: list[str] | None = None,
) -> str:
    """Replace a generic stall with a concrete next question; keep long substance."""
    asked = asked_titles or []
    follow, _ready = specific_followup_message(spec, pending, asked)
    notice = lock_notice_for_pending(pending)
    body = (text or "").strip()
    if not body:
        return follow
    lower = body.lower()
    still_arguing_dropped = bool(notice) and (
        (
            "synchronization" in notice.lower()
            and any(
                w in lower
                for w in (
                    "saga",
                    "outbox",
                    "eventual consistency",
                    "must design for synchronization",
                    "we must still implement",
                )
            )
        )
        or (
            "encryption" in notice.lower()
            and any(
                w in lower
                for w in ("dpapi", "keyring", "defense-in-depth", "must decouple")
            )
        )
    )
    if still_arguing_dropped:
        return follow
    if not is_vague_question(body):
        if notice and notice.strip()[:24].lower() not in lower:
            return f"{notice}{body}"
        return body
    core = re.split(r"\n\*\*Updates to this proposal\*\*", body, maxsplit=1)[0]
    core = re.split(r"\n\*\*What you can do next\*\*", core, maxsplit=1)[0].strip()
    if len(core.split()) < 40:
        return follow
    if "❓" not in core:
        return f"{core}\n\n{follow}"
    return follow


def format_asked_block(asked_titles: list[str]) -> str:
    if not asked_titles:
        return "(none yet)"
    return "\n".join(f"- {t}" for t in asked_titles)


def format_uncovered_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none — checklist looks covered in the living spec)"
    return "\n".join(f"- {item['title']} (id={item['id']})" for item in items)


_USER_STOP_PATTERNS = (
    re.compile(r"\bstop asking\b", re.I),
    re.compile(r"\bno more questions?\b", re.I),
    re.compile(r"\benough questions?\b", re.I),
    re.compile(r"\bdon'?t ask (me )?(any )?more\b", re.I),
    re.compile(r"\bplease stop\b", re.I),
    re.compile(r"\bi(?:'m| am) (done|ready)\b", re.I),
    re.compile(r"\bwe(?:'re| are) (done|ready)\b", re.I),
    re.compile(r"\bready to approve\b", re.I),
    re.compile(r"\blet me approve\b", re.I),
    re.compile(r"\ballow me to approve\b", re.I),
    re.compile(r"\benable approve\b", re.I),
    re.compile(r"\bapprove (the )?(business )?spec\b", re.I),
    re.compile(r"\bskip (the )?(rest of )?(the )?(interview|questions?)\b", re.I),
    re.compile(r"\bthat'?s all I have\b", re.I),
    re.compile(r"\bthat is all I have\b", re.I),
    re.compile(r"\bthat'?s all for now\b", re.I),
    re.compile(r"\bthat is all for now\b", re.I),
    re.compile(r"\bmove on\b", re.I),
    re.compile(r"\bproceed (to|with) (market|design|approval)\b", re.I),
    re.compile(r"\bgo ahead (and )?approve\b", re.I),
    re.compile(r"\bi want to approve\b", re.I),
    re.compile(r"\bcan i approve\b", re.I),
    re.compile(r"\bstop the interview\b", re.I),
    re.compile(r"\bend the interview\b", re.I),
)

_USER_APPROVE_ANYWAY_PATTERNS = (
    re.compile(r"\bapprove anyway\b", re.I),
    re.compile(r"\bproceed anyway\b", re.I),
    re.compile(r"\bcontinue anyway\b", re.I),
    re.compile(r"\bdesign anyway\b", re.I),
    re.compile(r"\bsketch anyway\b", re.I),
    re.compile(r"\bforce approve\b", re.I),
    re.compile(r"\bi understand[,.]? (approve|proceed|continue)\b", re.I),
    re.compile(r"\bgo ahead anyway\b", re.I),
)

# Minimum topics needed before an honest system-design sketch is feasible.
# Problem / opportunity is optional: a named product + job already states it.
_REQUIRED_FOR_SKETCH = ("actors", "scope")

_SKIP_QUESTION_PATTERNS = (
    re.compile(r"\bskip (this|that|the current) question\b", re.I),
    re.compile(r"\bskip (this|that) (one|topic)\b", re.I),
    re.compile(r"\blet'?s skip (this|that|it)\b", re.I),
    re.compile(r"\bpass on (this|that) question\b", re.I),
)


def user_requests_ready(text: str | None, last_assistant: str = "") -> bool:
    """True when the user explicitly asks to stop questioning / approve / move on."""
    body = (text or "").strip()
    if not body:
        return False
    from architect_agent.query_intent import classify_user_message

    if classify_user_message(body, last_assistant) == ("command", "approve"):
        return True
    return any(p.search(body) for p in _USER_STOP_PATTERNS) or user_requests_approve_anyway(
        body
    )


def user_skips_current_question(text: str | None, last_assistant: str = "") -> bool:
    """True when they want to drop this one interview question, not the whole interview."""
    body = (text or "").strip()
    if not body:
        return False
    if user_requests_ready(body, last_assistant):
        return False
    from architect_agent.query_intent import classify_user_message

    category, action = classify_user_message(body, last_assistant)
    if category == "information" or action == "approve":
        return False
    return any(p.search(body) for p in _SKIP_QUESTION_PATTERNS)


def is_interview_control_phrase(text: str | None) -> bool:
    """True for skip / stop / help-me-answer lines that must never become spec bullets."""
    body = (text or "").strip()
    if not body:
        return False
    from architect_agent.query_intent import looks_like_help_answering

    return (
        user_skips_current_question(body)
        or user_requests_ready(body)
        or looks_like_help_answering(body)
    )


_ACCEPT_RECOMMENDED_PATTERNS = (
    re.compile(r"\bas you recommended\b", re.I),
    re.compile(r"\bas recommended\b", re.I),
    re.compile(r"\byour recommendation\b", re.I),
    re.compile(r"\bgo with (your|the) recommend", re.I),
    re.compile(r"\blet'?s go with (that|your|the recommend)", re.I),
    re.compile(r"\bi(?:'ll| will) (take|go with) (that|your)", re.I),
    re.compile(r"\boption\s*1\b", re.I),
    re.compile(r"\bthe first (one|option)\b", re.I),
)

_CANNOT_ANSWER_PATTERNS = (
    re.compile(
        r"\bi don.?t have\b.{0,40}\b(concrete|specific|role|roles|answer|preference)",
        re.I,
    ),
    re.compile(
        r"\bi do not have\b.{0,40}\b(concrete|specific|role|roles|answer|preference)",
        re.I,
    ),
    re.compile(r"\bno concrete (role|roles|answer)", re.I),
    re.compile(r"\bno (particular|specific) (role|preference|answer)", re.I),
    re.compile(r"\bi (don.?t|do not) know what to (say|pick|choose)\b", re.I),
)

_RECOMMENDATION_BLOCK_RE = re.compile(
    r"(?im)^\s*(?:\*{0,2}Recommendation\*{0,2}|We recommend|I recommend)\s*[:\-]\s*(.+)"
)


def accepts_recommended_default(text: str | None) -> bool:
    """True when they accept the architect's recommended option instead of inventing one."""
    body = (text or "").strip()
    if not body:
        return False
    return any(p.search(body) for p in _ACCEPT_RECOMMENDED_PATTERNS)


def cannot_answer_current_question(text: str | None) -> bool:
    """True when they cannot name a concrete answer and we should take the default."""
    body = (text or "").strip()
    if not body:
        return False
    return any(p.search(body) for p in _CANNOT_ANSWER_PATTERNS)


def _is_short_approval(text: str | None, last_assistant: str = "") -> bool:
    body = (text or "").strip()
    if not body or len(body.split()) > 8:
        return False
    from architect_agent.query_intent import is_step_approval_message

    return is_step_approval_message(body, last_assistant)


_CONTROL_BULLET_RE = re.compile(
    r"(?im)^[-*]\s*(?:"
    r"skip this question(?: please)?"
    r"|skip (?:this|that) (?:one|topic|question)"
    r"|let'?s skip (?:this|that|it)"
    r"|that'?s all I have(?: for now)?"
    r"|can you suggest a response\??"
    r"|suggest a response"
    r")\s*$"
)


def scrub_control_phrases_from_spec(spec: str) -> str:
    """Drop skip/help control lines that leaked into the living spec."""
    cleaned = _CONTROL_BULLET_RE.sub("", spec or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + ("\n" if cleaned.strip() else "")


_RECOMMENDED_RE = re.compile(
    r"(?im)(?:➡️|→)?\s*\(\s*Recommended\s*\)\s*[:\-]?\s*(.+)"
)
_PAIN_QUESTION_RE = re.compile(
    r"(?i)(pain point|problem / opportunity|why (?:build|now)|worth building|who hurts)"
)


def extract_recommended_default(*texts: str) -> str:
    """Pull the recommended default from a question or last assistant turn."""
    for text in texts:
        for match in _RECOMMENDED_RE.finditer(text or ""):
            line = (match.group(1) or "").strip()
            if line:
                return line.splitlines()[0].strip()
    for text in texts:
        for match in _RECOMMENDATION_BLOCK_RE.finditer(text or ""):
            para = (match.group(1) or "").strip()
            if not para:
                continue
            parts = re.split(r"(?<=[.!?])\s+", para, maxsplit=1)
            taken = " ".join(part.strip() for part in parts if part.strip())
            return taken[:400] or para.splitlines()[0].strip()
    return ""


def lock_open_answer_into_spec(spec: str, last_assistant: str, pending: str) -> str:
    """Write this turn's answer or the accepted recommendation into the open section."""
    living = living_spec_scaffold(spec)
    pending_text = (pending or "").strip()
    if not pending_text:
        return living
    heading = heading_for_turn(last_assistant, pending_text)
    recommended = extract_recommended_default(last_assistant)
    from architect_agent.query_intent import (
        classify_user_message,
        is_accept_recommendation_message,
    )

    _category, action = classify_user_message(pending_text, last_assistant)
    take_default = (
        user_skips_current_question(pending_text)
        or cannot_answer_current_question(pending_text)
        or (bool(recommended) and is_accept_recommendation_message(pending_text, last_assistant))
        or (action == "approve" and bool(recommended))
    )
    if take_default:
        if recommended:
            living = append_spec_bullet(living, heading, recommended)
        elif user_skips_current_question(pending_text) or cannot_answer_current_question(
            pending_text
        ):
            living = apply_skipped_question(living, last_assistant, last_assistant)
        return scrub_control_phrases_from_spec(living)
    if is_interview_control_phrase(pending_text) or _is_short_approval(
        pending_text, last_assistant
    ):
        return living
    return append_spec_bullet(living, heading, pending_text[:400])


def spec_still_scaffold(spec: str) -> bool:
    """True when discovery sections are still placeholders."""
    body = spec or ""
    placeholders = len(re.findall(r"(?i)to be (?:captured|classified after discovery)", body))
    return placeholders >= 3


def hydrate_spec_from_transcript(
    spec: str,
    messages: Iterable[Any] | None,
    digest: str = "",
) -> str:
    """Replay Phase 0 answers onto a scaffold that never absorbed the interview."""
    del digest
    living = living_spec_scaffold(spec)
    last_assistant = ""
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        role = str(msg.get("role") or "")
        if role == "assistant":
            last_assistant = content
        elif role == "user":
            living = lock_open_answer_into_spec(living, last_assistant, content)
    return scrub_control_phrases_from_spec(living)


def is_pain_opportunity_question(text: str) -> bool:
    """True when a question is the optional business-case / why-build prompt."""
    return bool(_PAIN_QUESTION_RE.search(text or ""))


def apply_skipped_question(spec: str, question_text: str, last_assistant: str = "") -> str:
    """Record the recommended default so a skipped question does not stay open."""
    default = extract_recommended_default(question_text, last_assistant)
    if not default:
        default = (
            "Accepted the recommended default for this question; no extra v1 constraint."
        )
    topic = question_topic_id(question_text)
    if topic == "problem" or is_pain_opportunity_question(question_text):
        heading = "## Problem"
    elif topic == "actors":
        heading = "## Actors"
    elif topic == "scope":
        heading = "## In scope (v1)"
    elif topic == "nongoals":
        heading = "## Out of scope"
    elif topic == "invariants":
        heading = "## Critical invariants"
    elif topic == "success":
        heading = "## Success criteria"
    elif topic == "assumptions":
        heading = "## Assumptions & risks"
    else:
        heading = guess_spec_section(question_text + " " + default)
    return append_spec_bullet(spec, heading, default)


def user_requests_approve_anyway(text: str | None) -> bool:
    """True when the user accepts proceeding despite an incomplete spec."""
    if not text or not str(text).strip():
        return False
    return any(p.search(str(text)) for p in _USER_APPROVE_ANYWAY_PATTERNS)


def missing_for_design_sketch(spec: str) -> list[dict[str, Any]]:
    """Checklist items that are still too thin to support a credible design sketch."""
    missing: list[dict[str, Any]] = []
    for item in CHECKLIST:
        if item["id"] not in _REQUIRED_FOR_SKETCH:
            continue
        if not _section_has_substance(spec, tuple(item["section_hints"])):
            missing.append(item)
    return missing


def enough_to_sketch_design(spec: str) -> bool:
    return not missing_for_design_sketch(spec)


def message_for_user_stop(spec: str, *, approve_anyway: bool = False) -> tuple[str, bool]:
    """
    Build the reply when the user stops the interview.
    Returns (assistant_message, ready_for_design).
    """
    missing = missing_for_design_sketch(spec)
    if not missing:
        return (
            "Understood — I'll stop asking interview questions.\n\n"
            "The living spec looks sufficient to sketch a system design. "
            "If this looks right, confirm, approve, or agree so we can continue "
            "(market evaluation, then system design).",
            True,
        )

    gaps = "\n".join(f"- **{item['title']}**" for item in missing)
    if approve_anyway:
        return (
            "Understood — proceeding despite gaps.\n\n"
            "Honestly, the current spec is still thin for a solid design sketch. "
            "Missing or under-specified:\n"
            f"{gaps}\n\n"
            "I can still continue, but the first design will be **speculative** and may "
            "need heavy revision. Confirm, approve, or agree if you want to continue anyway.",
            True,
        )

    return (
        "Understood — I'll stop asking interview questions.\n\n"
        "Honestly, I do **not** have enough information yet to sketch a credible system design. "
        "Still missing or too vague:\n"
        f"{gaps}\n\n"
        "I won't invent another grill question unless you want one. "
        "You can:\n"
        "- reply with detail on those gaps, or\n"
        "- say **approve anyway** if you want a speculative first design draft.",
        False,
    )


_SCAFFOLD_HINTS = ("## problem", "## actors", "## in scope")
_PLACEHOLDER_BULLET_RE = re.compile(
    r"(?im)^-\s*(?:\(to be (?:captured|classified after discovery)\)|to be refined|tbd)\s*$"
)


def living_spec_scaffold(spec: str) -> str:
    """Turn a one-line request into a sectioned living spec the UI can update in place."""
    body = (spec or "").strip()
    lower = body.lower()
    if sum(1 for hint in _SCAFFOLD_HINTS if hint in lower) >= 2:
        return body
    problem = body or "To be refined during discovery."
    if len(problem) > 2000:
        problem = problem[:2000].rstrip() + "…"
    return (
        "# Business Specification\n\n"
        f"## Problem\n{problem}\n\n"
        "## Actors\n- (to be captured)\n\n"
        "## Goals\n- (to be captured)\n\n"
        "## In scope (v1)\n- (to be captured)\n\n"
        "## Out of scope\n- (to be captured)\n\n"
        "## Deployment topology\n- (to be classified after discovery)\n\n"
        "## Critical invariants\n- (to be captured)\n\n"
        "## Success criteria\n- (to be captured)\n\n"
        "## Assumptions & risks\n- (to be captured)\n"
    )


_TOPIC_HEADINGS = {
    "problem": "## Problem",
    "actors": "## Actors",
    "scope": "## In scope (v1)",
    "nongoals": "## Out of scope",
    "invariants": "## Critical invariants",
    "success": "## Success criteria",
    "assumptions": "## Assumptions & risks",
}


def heading_for_turn(question: str, answer: str) -> str:
    """Pick the living-spec heading from the open question, then the answer."""
    topic = question_topic_id(question)
    if topic and topic in _TOPIC_HEADINGS:
        return _TOPIC_HEADINGS[topic]
    q = (question or "").lower()
    if any(
        token in q
        for token in ("scale", "capacity", "dau", "qps", "concurrent", "storage volume")
    ):
        return "## Assumptions & risks"
    return guess_spec_section(answer)


def spec_substance(spec: str) -> int:
    """Count characters that are not scaffold placeholders."""
    body = re.sub(
        r"(?i)to be (?:captured|classified after discovery|refined)|tbd",
        "",
        spec or "",
    )
    return len(re.sub(r"\s+", "", body))


def guess_spec_section(answer: str) -> str:
    """Pick the living-spec heading a decision should land under."""
    lower = (answer or "").lower()
    if any(
        token in lower
        for token in (
            "gdpr",
            "residenc",
            "eu user",
            "eu-only",
            "compliance",
            "must never",
            "invariant",
            "safety",
            "trust",
            "security",
        )
    ):
        return "## Critical invariants"
    if any(
        token in lower
        for token in (
            "ignor",
            "skip",
            "not build",
            "out of scope",
            "don't need",
            "do not need",
            "dont need",
            "not in v1",
            "no remote",
            "no extra",
            "synchronization",
        )
    ):
        return "## Out of scope"
    if any(
        token in lower
        for token in ("actor", "user", "clerk", "operator", "who uses", "customer", "role")
    ):
        return "## Actors"
    if any(token in lower for token in ("success", "metric", "kpi", "done when", "know v1")):
        return "## Success criteria"
    if any(
        token in lower
        for token in ("stand-alone", "standalone", "self-contained", "distributed", "deploy")
    ):
        return "## Deployment topology"
    if any(token in lower for token in ("must ship", "capability", "in scope", "v1")):
        return "## In scope (v1)"
    return "## Goals"


def append_spec_bullet(spec: str, heading: str, text: str) -> str:
    """Add a decision bullet under a markdown heading without dropping earlier bullets."""
    body = living_spec_scaffold(spec)
    bullet = re.sub(r"\s+", " ", (text or "").strip()).strip(" -")
    if (
        not bullet
        or is_interview_control_phrase(bullet)
        or accepts_recommended_default(bullet)
        or cannot_answer_current_question(bullet)
        or _is_short_approval(bullet)
    ):
        return body
    line = f"- {bullet}"
    compact = line.lower()
    if compact[:96] in body.lower():
        return body
    if heading not in body:
        return body.rstrip() + f"\n\n{heading}\n{line}\n"
    idx = body.find(heading)
    rest = body[idx + len(heading) :]
    nxt = re.search(r"\n## ", rest)
    section = rest if nxt is None else rest[: nxt.start()]
    after = "" if nxt is None else rest[nxt.start() :]
    stripped = section.rstrip()
    if _PLACEHOLDER_BULLET_RE.search(stripped):
        stripped = _PLACEHOLDER_BULLET_RE.sub(line, stripped, count=1)
    else:
        stripped = f"{stripped}\n{line}"
    tail = after.lstrip("\n")
    return f"{body[:idx]}{heading}{stripped}\n{tail}"


def record_dropped_constraints(spec: str, pending: str) -> str:
    """Write confirmed drop-decisions into Out of scope so the artifact matches chat."""
    notice = lock_notice_for_pending(pending)
    if not notice:
        return spec
    updated = spec
    lower = (pending or "").lower()
    if "sync" in lower:
        updated = append_spec_bullet(
            updated,
            "## Out of scope",
            "No remote / cross-device synchronization in v1",
        )
    if "secur" in lower:
        updated = append_spec_bullet(
            updated,
            "## Out of scope",
            "No extra credential-vault or encryption work in v1 beyond ordinary OS file access",
        )
    return updated
