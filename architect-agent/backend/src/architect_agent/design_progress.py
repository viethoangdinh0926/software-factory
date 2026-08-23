"""Linear design-round progress: no skip-ahead, rewind when a change belongs earlier."""

from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from typing import Any

from architect_agent.query_intent import is_revision_request, with_next_prompt
from architect_agent.scope import wants_standalone

logger = logging.getLogger(__name__)

NEW_ROUND_AFTER_HANDOFF = (
    "A new design round starts at **Phase 0**. Tell me any spec or scope updates, "
    "or confirm, approve, or agree if you want to walk the classified track again "
    "from scope. We still go one confirmed step at a time — no jumping ahead."
)

SKIP_AHEAD_REPLY = (
    "We walk the design one confirmed step at a time. I cannot jump ahead to a "
    "later phase until its prior steps are confirmed. Let's finish this step first."
)

NO_UPDATES_TO_DELIVER = (
    "There are no design updates since the last package delivered to the Orchestrator, "
    "so I am not sending a duplicate. A new round starts at **Phase 0**. "
    "Tell me any spec or design changes, or confirm, approve, or agree to walk the "
    "track again."
)

KEEP_AND_PATCH_RULES = (
    "KEEP EXISTING ARTIFACTS. Work already completed on this step and later steps "
    "is the source of truth. Do not regenerate this step from scratch.\n"
    "If this step already has a complete primary artifact, keep it and apply only "
    "the deltas required by the carry-forward user/spec change (if any).\n"
    "If that change does not affect this step, return \"\" for the primary field "
    "(the server keeps the existing copy) and say you kept it.\n"
    "Leave non-primary fields as \"\" / design_diagram_lines as [] so the server "
    "preserves prior values.\n"
)

_PACKAGE_META_LINE_RE = re.compile(
    r"^(Design session:|Design version:|Track:|Generated:)\s"
)

_REWIND_SYSTEM = (
    "You are the DESIGN-STAGE ROUTER for the architect agent.\n"
    "The user sent a change request during an in-progress design round.\n"
    "Decide which earliest already-passed stage that change belongs to.\n"
    "Stages, in order:\n"
    "- phase0: business spec, actors, jobs, v1 scope, non-goals, invariants, "
    "success metrics, LLD vs HLD classification, new product requirements.\n"
    "- lld1 / hld1: LLD information gathering, or HLD capacity/scale (DAU, QPS, SLAs).\n"
    "- lld2 / hld2: LLD class/blueprint, or HLD domain objects / entities.\n"
    "- lld3 / hld3: LLD verification, or HLD core microservices.\n"
    "- hld4: communication schemes, infra, system diagram.\n"
    "- hld5: FMEA / failure modes.\n"
    "- hld6: session synthesis.\n"
    "- market: comments only about the market evaluation report/grade.\n"
    "- current: the change belongs on THIS step (do not rewind).\n"
    "- ahead: they asked to skip or jump to a later stage. Never allow that.\n"
    "If unsure between two earlier stages, pick the earlier one.\n"
    "A new v1 requirement, actor, invariant, or non-goal is phase0 even if they "
    "are currently modeling domain objects or services.\n"
    "Switching to a local, self-contained, stand-alone, desktop, single-machine, or "
    "single-process app — or away from distributed/microservices — is phase0 "
    "(reclassify LLD vs HLD). Analogies like YouTube do not keep the session on HLD.\n"
    "Respond ONLY with JSON: {\"stage\":\"phase0|lld1|hld1|...|market|current|ahead\"}"
)

_VALID_STAGES = frozenset(
    {
        "phase0",
        "lld1",
        "lld2",
        "lld3",
        "hld1",
        "hld2",
        "hld3",
        "hld4",
        "hld5",
        "hld6",
        "market",
        "current",
        "ahead",
    }
)


def max_track_step(track: str) -> int:
    if track == "lld":
        return 3
    if track == "hld":
        return 6
    return 0


def design_position(phase: str, track: str, step: int) -> int:
    """Comparable cursor: Phase 0 = 0, track steps 1..N, market = N+1."""
    cap = max_track_step(track) or 6
    if phase == "phase0" or int(step or 0) <= 0:
        return 0
    if phase == "market_research":
        return cap + 1
    if phase in {"lld", "hld"}:
        return max(1, min(cap, int(step)))
    return 0


def stage_to_phase_step(stage: str, track: str) -> tuple[str, str, int] | None:
    """Map a router stage to (phase, track, step). None = stay / invalid."""
    s = (stage or "").strip().lower()
    if s in {"", "current", "ahead"}:
        return None
    if s == "phase0":
        return "phase0", track if track in {"lld", "hld"} else "unset", 0
    if s == "market":
        return "market_research", track if track in {"lld", "hld"} else "hld", max_track_step(track) or 6
    m = re.fullmatch(r"(lld|hld)([1-6])", s)
    if not m:
        return None
    dest_track = m.group(1)
    dest_step = int(m.group(2))
    if dest_track == "lld" and dest_step > 3:
        return None
    return dest_track, dest_track, dest_step


def stage_label(phase: str, track: str, step: int) -> str:
    if phase == "phase0":
        return "Phase 0 — scope & spec"
    if phase == "market_research":
        return "Market evaluation"
    titles = {
        ("lld", 1): "Information gathering",
        ("lld", 2): "Architectural blueprint",
        ("lld", 3): "Verification",
        ("hld", 1): "Requirements & capacity estimation",
        ("hld", 2): "Domain object modeling",
        ("hld", 3): "Core microservices",
        ("hld", 4): "Communication schemes, infrastructure & system diagram",
        ("hld", 5): "Vulnerability & edge-case analysis (FMEA)",
        ("hld", 6): "Session synthesis & wrap-up",
    }
    prefix = (track or phase or "").upper()
    title = titles.get((track, step), "")
    if step:
        return f"{prefix} step {step} — {title}" if title else f"{prefix} step {step}"
    return title or prefix


def _heuristic_rewind_stage(text: str, track: str) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not compact:
        return "current"
    if re.search(r"\b(skip|jump)\s+(ahead|to)\b|\bgo\s+straight\s+to\b|\bjump\s+to\s+step\b", compact):
        return "ahead"
    if wants_standalone(compact):
        return "phase0"
    if any(
        token in compact
        for token in (
            "gdpr",
            "residenc",
            "new requirement",
            "spec requirement",
            "v1 must",
            "out of scope",
            "non-goal",
            "non goal",
            "primary actor",
            "new actor",
            "invariant",
            "success criteria",
            "who uses",
        )
    ):
        return "phase0"
    if track == "lld":
        if any(token in compact for token in ("class diagram", "solid", "blueprint", "pattern")):
            return "lld2"
        if any(token in compact for token in ("business rule", "concurrency", "lifecycle")):
            return "lld1"
        return "current"
    if any(token in compact for token in ("dau", "qps", "capacity", "sla", "p99", "bandwidth")):
        return "hld1"
    if any(token in compact for token in ("domain object", "entity", "entities", "bounded context")):
        return "hld2"
    if any(token in compact for token in ("microservice", "bounded service", "service split")):
        return "hld3"
    if any(token in compact for token in ("diagram", "kafka", "cdn", "gateway", "communication scheme")):
        return "hld4"
    if any(token in compact for token in ("fmea", "spof", "split-brain", "race condition")):
        return "hld5"
    if "grade" in compact or "market" in compact:
        return "market"
    return "current"


def _llm_rewind_stage(text: str, context: str) -> str | None:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from architect_agent.json_util import parse_llm_json_object
        from architect_agent.llm import get_chat_model

        model = get_chat_model()
        response = model.invoke(
            [
                SystemMessage(content=_REWIND_SYSTEM),
                HumanMessage(
                    content=(
                        f"Current position:\n{context or '(none)'}\n\n"
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
        stage = str(parsed.get("stage") or "").strip().lower()
        if stage in _VALID_STAGES:
            return stage
        return None
    except Exception as exc:
        logger.warning("Rewind-stage router failed; using fallback: %s", exc)
        return None


@lru_cache(maxsize=256)
def classify_rewind_stage(text: str, context: str = "", track: str = "hld") -> str:
    raw = (text or "").strip()
    if not raw:
        return "current"
    classified = _llm_rewind_stage(raw, context)
    if classified is not None:
        return classified
    return _heuristic_rewind_stage(raw, track)


def package_fingerprint(markdown: str) -> str:
    """Stable hash of a design package, ignoring version/timestamp metadata."""
    body = "\n".join(
        line
        for line in (markdown or "").splitlines()
        if not _PACKAGE_META_LINE_RE.match(line)
    )
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def should_keep_and_patch(state: dict[str, Any], step: int) -> bool:
    """True when this step was already completed and we are walking it again."""
    until = int(state.get("rewalk_until_step") or 0)
    if until <= 0:
        return False
    return max(1, int(step or 0)) <= until


def keep_or_patch(new: str, old: str) -> str:
    """Prefer a non-empty patch; otherwise keep the prior artifact."""
    new_s = (new or "").strip()
    if new_s:
        return new
    return old or ""


def with_rewind_notice(message: str, notice: str) -> str:
    notice = (notice or "").strip()
    body = (message or "").strip()
    if not notice:
        return body
    if notice in body:
        return body
    if not body:
        return notice
    return f"{notice}\n\n{body}"


def rewind_or_block_skip(
    state: dict[str, Any],
    user_text: str,
    *,
    node: str,
    current_phase: str,
    current_track: str,
    current_step: int,
    msgs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """If the update belongs earlier, rewind. If they asked to jump ahead, refuse.

    Returns a wait-node result dict, or None to keep revising the current step.
    """
    text = (user_text or "").strip()
    if not text:
        return None
    track = current_track if current_track in {"lld", "hld"} else "hld"
    here = design_position(current_phase, track, current_step)
    context = f"phase={current_phase} track={track} step={current_step}"
    stage = classify_rewind_stage(text, context, track)
    dest = stage_to_phase_step(stage, track)
    dest_pos = (
        design_position(dest[0], dest[1], dest[2]) if dest is not None else here
    )
    revising = is_revision_request(text)
    skip_ahead = stage == "ahead" or (
        revising and dest is not None and dest_pos > here and here > 0
    )
    if skip_ahead:
        reply = with_next_prompt(SKIP_AHEAD_REPLY)
        chat_node = current_phase if current_phase in {"phase0", "lld", "hld", "market_research"} else node
        return {
            "phase": current_phase,
            "design_track": current_track if current_track in {"unset", "lld", "hld"} else track,
            "design_step": current_step,
            "ready_to_advance": bool(state.get("ready_to_advance")),
            "design_ready_to_approve": bool(state.get("design_ready_to_approve")),
            "pending_user_feedback": "",
            "pending_assistant_message": reply,
            "stay_on_interrupt": True,
            "publish_requested": False,
            "discussion_digest": str(state.get("discussion_digest") or ""),
            "messages": msgs
            + [{"role": "assistant", "content": reply, "node": chat_node}],
        }
    if not revising or here <= 0 or dest is None or dest_pos >= here:
        return None
    dest_phase, dest_track, dest_step = dest
    label = stage_label(dest_phase, dest_track, dest_step)
    notice = (
        f"This update belongs to **{label}**, so we are returning there and will "
        "walk forward again one confirmed step at a time. Later-step artifacts stay "
        "in place; we will patch them only where this change requires it."
    )
    until = here
    if current_phase == "market_research":
        until = max_track_step(track)
    out: dict[str, Any] = {
        "phase": dest_phase,
        "design_track": dest_track if dest_track in {"unset", "lld", "hld"} else track,
        "design_step": dest_step,
        "ready_to_advance": False,
        "design_ready_to_approve": False,
        "pending_user_feedback": text,
        "pending_assistant_message": notice,
        "rewind_notice": notice,
        "carry_change": text,
        "rewalk_until_step": until,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "discussion_digest": str(state.get("discussion_digest") or ""),
        "messages": msgs,
    }
    if dest_phase == "phase0":
        out["interview_complete"] = True
        out["spec_compiled"] = True
    return out
