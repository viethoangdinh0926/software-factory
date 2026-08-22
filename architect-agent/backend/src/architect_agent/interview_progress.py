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
_REQUIRED_FOR_SKETCH = ("problem", "actors", "scope")


def user_requests_ready(text: str | None) -> bool:
    """True when the user explicitly asks to stop questioning / approve / move on."""
    if not text or not str(text).strip():
        return False
    return any(p.search(str(text)) for p in _USER_STOP_PATTERNS) or user_requests_approve_anyway(
        text
    )


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
