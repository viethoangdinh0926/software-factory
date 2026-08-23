"""Shared LLM helpers and service-list utilities."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from orchestrator_agent.json_util import parse_llm_json_object
from orchestrator_agent.llm import get_chat_model
from orchestrator_agent.query_intent import (
    SUGGESTED_ANSWER_RULES,
    with_next_prompt,
    with_resolution_close,
)

ProseRecover = Callable[[str], dict[str, Any] | None]

logger = logging.getLogger(__name__)

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`.\n"
    "Keep assistant_message's full elaborated justification; escape its newlines as \\n. "
    "Do not use LaTeX ($\\text{...}$ / $\\approx$); write plain text or unicode."
)

# Appended to skill_digest() so every orchestrator prompt inherits the same depth bar:
# user-visible messages must justify decisions, not just announce them.
EXPLANATION_DEPTH_DIGEST = """
CHAT DEPTH (assistant_message) — write like a Staff Engineer briefing the team:
- Target 200-500 words of substance. NEVER a bare status line such as "Plan updated.",
  "Tech stack selected." or "Extracted the services." A recap with no reasoning is a
  FAILED turn.
- Every recommendation you surface (topology, entity relationships, features, tech stack,
  plan spec, service split) must justify itself by covering:
  1. WHAT you decided, naming concrete elements — service names, related entities,
     who initiates each relationship, libraries with their role, data stores —
     not "the stack" or "the design". Do not lock REST vs gRPC vs topic catalogs.
  2. WHY it fits THIS service/system: the driving forces from the architect package
     (owned objects, communication schemes as context, traffic profile, latency, consistency).
  3. ALTERNATIVES considered and the explicit reason each was rejected.
  4. TRADE-OFFS accepted — operational cost, added latency, lock-in, learning curve —
     and why they are acceptable here.
  5. ASSUMPTIONS made where the package was silent, each labeled with your default.
  6. IMPLICATIONS for the downstream engineer implementing this.
- Teach while you decide: name the pattern or principle you are applying (idempotency
  keys, outbox, saga, circuit breaker, CQRS, backpressure, twelve-factor) and say in one
  clause what it buys here. Prefer concrete versions/numbers over vague adjectives.
- Use tight markdown structure (bold lead-ins, short bullets) so it stays scannable.
  Depth means information density, NOT padding — no filler, no restating the question.
- Still ask them to confirm, approve, or agree once the artifact is ready. Never tell
  them to click a button. Elaboration replaces terseness; it does not replace the
  approve flow.
""".strip()

APPROVE_LABELS = {
    "confirm_topology": "Confirm topology",
    "approve_relations": "Approve relationships",
    "approve_comms": "Approve relationships",
    "approve_features": "Approve features",
    "decide_api_type": "Approve relationships",
    "approve_api_design": "Approve relationships",
    "approve_plan": "Approve plan",
    "approve_spec_update": "Ship spec update",
}

STATUS_APPROVE_KIND = {
    "awaiting_relations": "approve_relations",
    "awaiting_comms": "approve_relations",
    "awaiting_api_type": "approve_relations",
    "awaiting_api_design": "approve_relations",
    "awaiting_features": "approve_features",
    "awaiting_stack": "approve_plan",
    "awaiting_spec_update": "approve_spec_update",
}


def features_are_concrete(text: str) -> bool:
    """Require a real v1 feature list, not a one-line sketch."""
    body = (text or "").strip()
    if len(body) < 280:
        return False
    bullets = len(re.findall(r"(?m)^\s*[-*]", body))
    numbered = len(re.findall(r"(?m)^\s*\d+\.", body))
    return (bullets + numbered) >= 4


def bugs_are_concrete(text: str) -> bool:
    """A usable bug list: at least one real item, not a placeholder."""
    body = (text or "").strip()
    if len(body) < 40:
        return False
    lower = body.lower()
    if lower in {"(none)", "none", "n/a", "- none"}:
        return False
    bullets = len(re.findall(r"(?m)^\s*[-*]", body))
    numbered = len(re.findall(r"(?m)^\s*\d+\.", body))
    headings = len(re.findall(r"(?m)^#{2,3}\s+", body))
    return (bullets + numbered + headings) >= 1


def spec_delta_is_ready(svc: dict[str, Any] | None) -> bool:
    """True when features or bugs changed since the last engineer ship."""
    if not svc:
        return False
    features = str(svc.get("feature_spec") or "").strip()
    bugs = str(svc.get("bug_spec") or "").strip()
    shipped_features = str(svc.get("shipped_feature_spec") or "").strip()
    shipped_bugs = str(svc.get("shipped_bug_spec") or "").strip()
    features_changed = features != shipped_features
    bugs_changed = bugs != shipped_bugs
    if not features_changed and not bugs_changed:
        return False
    if features_changed and features and not features_are_concrete(features):
        return False
    if bugs_changed and bugs and not bugs_are_concrete(bugs) and not features_are_concrete(features):
        return False
    return bool(features_are_concrete(features) or bugs_are_concrete(bugs))


def relation_artifact(svc: dict[str, Any] | None) -> str:
    """Entity-relationship markdown for a service (legacy api_design is a fallback)."""
    if not svc:
        return ""
    return str(svc.get("entity_relationships") or svc.get("api_design") or "").strip()


def relations_are_concrete(text: str) -> bool:
    """Require a real inventory of related entities and who initiates each link."""
    body = (text or "").strip()
    if len(body) < 280:
        return False
    headings = len(re.findall(r"(?m)^#{2,3}\s+", body))
    bullets = len(re.findall(r"(?m)^\s*[-*]", body))
    lower = body.lower()
    has_entity = any(
        k in lower
        for k in (
            "user",
            "client",
            "infra",
            "postgres",
            "datastore",
            "gateway",
            "microservice",
            "peer",
            "kafka",
            "object store",
        )
    )
    has_dir = any(
        k in lower
        for k in ("initiate", "initiator", "calls", "invokes", "depends", "relationship")
    )
    return headings >= 2 and bullets >= 4 and has_entity and has_dir


def comms_are_concrete(text: str) -> bool:
    """Backward-compatible alias: relationships, not protocol catalogs."""
    return relations_are_concrete(text)


def approve_label(kind: str) -> str:
    return APPROVE_LABELS.get(kind, "Approve")


def service_step_kind(status: str) -> str:
    return STATUS_APPROVE_KIND.get(status, "")


def decorate_service(svc: dict[str, Any], *, finalized: bool = False) -> dict[str, Any]:
    status = str(svc.get("status") or "")
    kind = service_step_kind(status)
    suspended = status == "suspended"
    open_disc = (not finalized) and (not suspended)
    out = dict(svc)
    out["can_approve"] = bool(kind) and open_disc
    if status == "awaiting_features" and not features_are_concrete(out.get("feature_spec") or ""):
        out["can_approve"] = False
    if status == "awaiting_spec_update" and not spec_delta_is_ready(out):
        out["can_approve"] = False
    if status in {
        "awaiting_relations",
        "awaiting_comms",
        "awaiting_api_type",
        "awaiting_api_design",
    } and not relations_are_concrete(relation_artifact(out)):
        out["can_approve"] = False
    out["approve_kind"] = kind if out["can_approve"] else ""
    out["approve_label"] = approve_label(kind) if out["can_approve"] else ""
    out["discussion_open"] = open_disc
    return out


def close_user_message(
    message: str,
    *,
    approve_kind: str = "",
    can_approve: bool | None = None,
    mode: str = "step",
    svc: dict[str, Any] | None = None,
) -> str:
    """Append a next-action prompt using the current step or tile state."""
    if svc is not None:
        decorated = decorate_service(svc)
        status = str(svc.get("status") or "")
        use_mode = "handoff" if status in {"sent", "approved"} else mode
        return with_next_prompt(
            message,
            approve_label=str(decorated.get("approve_label") or ""),
            can_approve=bool(decorated.get("can_approve")),
            mode=use_mode,
        )
    label = approve_label(approve_kind) if approve_kind else ""
    approve = bool(approve_kind) if can_approve is None else can_approve
    return with_next_prompt(
        message,
        approve_label=label,
        can_approve=approve,
        mode=mode,
    )


def invoke_json(
    system: str,
    user: str,
    *,
    recover_prose: ProseRecover | None = None,
) -> dict[str, Any]:
    model = get_chat_model()
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    content = _invoke_content(model, messages)
    try:
        return parse_llm_json_object(content)
    except ValueError as first_exc:
        logger.warning("LLM JSON parse failed; retrying once: %s", first_exc)
        retry_messages = [
            SystemMessage(content=system),
            HumanMessage(content=user),
            SystemMessage(content=_RETRY_HINT),
            HumanMessage(content=f"Retry now. Preview of bad reply:\n{content[:900]}"),
        ]
        content2 = _invoke_content(model, retry_messages)
        try:
            return parse_llm_json_object(content2)
        except ValueError as second_exc:
            for blob in (content2, content):
                if recover_prose is not None:
                    recovered = recover_prose(blob)
                    if recovered is not None:
                        logger.warning(
                            "LLM JSON parse failed after retry; using prose recovery: %s",
                            second_exc,
                        )
                        return recovered
            raise ValueError(f"LLM JSON parse failed after retry: {second_exc}") from first_exc


def _invoke_content(model: Any, messages: list[Any]) -> str:
    response = model.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content or "")


def active_service(state: dict[str, Any]) -> dict[str, Any] | None:
    sid = str(state.get("active_service_id") or "")
    for svc in state.get("services") or []:
        if str(svc.get("microservice_id")) == sid:
            return svc
    return None


def replace_service(services: list[dict[str, Any]], updated: dict[str, Any]) -> list[dict[str, Any]]:
    sid = str(updated.get("microservice_id") or "")
    out: list[dict[str, Any]] = []
    found = False
    for svc in services:
        if str(svc.get("microservice_id")) == sid:
            out.append(updated)
            found = True
        else:
            out.append(svc)
    if not found:
        out.append(updated)
    return out


def empty_service(
    *,
    microservice_id: str,
    name: str,
    role_key: str,
    contract: str,
    names: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "microservice_id": microservice_id,
        "names": names or [name],
        "role_key": role_key,
        "architect_api_contract": contract,
        "feature_spec": "",
        "bug_spec": "",
        "spec_version": 0,
        "spec_changelog": "",
        "shipped_feature_spec": "",
        "shipped_bug_spec": "",
        "update_kind": "",
        "entity_relationships": "",
        "api_type": "",
        "api_type_recommendation": "",
        "proposed_api_type": "",
        "api_design": "",
        "tech_stack": "",
        "plan_spec": "",
        "status": "planning",
        "messages": [],
        "search_notes": "",
    }


def _as_visible_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (_as_visible_text(item) for item in value) if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "message", "markdown", "assistant_message"):
            text = _as_visible_text(value.get(key))
            if text:
                return text
        return ""
    return str(value).strip()


def pick_assistant_message(
    payload: dict[str, Any] | None,
    *,
    fallback: str,
    artifact_keys: tuple[str, ...] = (),
    preview_limit: int = 1600,
) -> str:
    """Never return empty: models often fill the artifact and leave assistant_message blank."""
    del preview_limit
    data = payload or {}
    for key in ("assistant_message", "rationale", "summary", "message"):
        text = _as_visible_text(data.get(key))
        if text:
            return text
    for key in artifact_keys:
        text = _as_visible_text(data.get(key))
        if text:
            return text
    return (
        fallback.strip()
        or "Updated this service. Reply if you want changes, or approve to continue."
    )


def _qa_recover(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    return {"assistant_message": body}


def answer_current_artifacts(
    *,
    question: str,
    artifacts: str,
    system_extra: str = "",
) -> str:
    """Answer a question from current artifacts without rewriting the step."""
    extra = f"{system_extra.strip()}\n" if system_extra.strip() else ""
    extra = f"{SUGGESTED_ANSWER_RULES}\n{extra}"
    result = invoke_json(
        system=(
            "You are answering a question about the current workflow step before the user "
            "approves it.\n"
            f"{extra}"
            "Answer with concrete facts from the artifacts. Quote METHOD /path, stack "
            "choices, and names when relevant.\n"
            f"{EXPLANATION_DEPTH_DIGEST}\n"
            "Since this turn is an answer (not an artifact rewrite): explain the reasoning "
            "behind whatever they asked about — why it is shaped that way, what the "
            "alternatives were, and what it costs — so they learn the architecture, not "
            "just its current values.\n"
            "Do not ask them to confirm in place of the answer. Do not say you finalized or updated anything.\n"
            'Respond ONLY with JSON: {"assistant_message": string}'
        ),
        user=f"Current artifacts:\n{artifacts}\n\nUser question:\n{question}",
        recover_prose=_qa_recover,
    )
    return with_resolution_close(
        pick_assistant_message(
            result,
            fallback="I can answer from the current artifacts on this step. Ask about a specific detail.",
            artifact_keys=(),
        ),
        changed=False,
    )


def close_after_feedback(message: str, *, pending: str, changed: bool) -> str:
    if not (pending or "").strip():
        return message
    return with_resolution_close(message, changed=changed)


def skill_digest() -> str:
    from orchestrator_agent.config import get_settings

    path = get_settings().skill_path
    try:
        text = path.read_text(encoding="utf-8")[:6000]
    except OSError:
        text = "You are the Software Factory Orchestrator."
    return f"{text}\n\n{EXPLANATION_DEPTH_DIGEST}"


def service_focus_system(name: str) -> str:
    """System prefix so a planning tile stays on one microservice."""
    return (
        f"You are planning ONE microservice: {name}. This tile is not the platform design.\n"
        f"Stay on {name}'s related entities, features, behavior, and its own data/runtime.\n"
        "Do not restate overall architecture. Do not design other microservices.\n"
        "Do not dictate communication schemes, protocols, or API catalogs (METHOD /path, "
        "gRPC RPCs, Kafka topics). Those are owned later by engineer sub-agents.\n"
        "Name related entities (users, peer core microservices, infra) and who initiates "
        "each relationship. Peer services appear only as collaborators to invoke.\n"
        "If the user asked a question, answer it in assistant_message with concrete facts "
        "(entity names, who initiates, what data/events flow). Do not reply with a status recap.\n"
        "If they raised a concern or asked to change something, update this service's artifact, "
        "list **Updates to this proposal**, then ask them to confirm, approve, or agree "
        "for that version. Never tell them to click a button."
    )


def service_focus_user_block(
    state: dict[str, Any],
    svc: dict[str, Any],
    *,
    pending: str = "",
    extra: str = "",
) -> str:
    """User payload with this service's contract, peers, and tile chat — not the full package."""
    from orchestrator_agent.package_parse import service_comms_excerpt, service_contract_section

    names = [str(n) for n in (svc.get("names") or []) if n]
    name = names[-1] if names else "Service"
    peers = [
        str((s.get("names") or ["peer"])[-1])
        for s in (state.get("services") or [])
        if s.get("status") != "suspended" and s.get("microservice_id") != svc.get("microservice_id")
    ]
    contract = str(svc.get("architect_api_contract") or "").strip()
    features = str(svc.get("feature_spec") or "").strip()
    package = str(state.get("package_markdown") or "")
    section = service_contract_section(package, names) or contract
    comms = service_comms_excerpt(package, names)
    spec = str(svc.get("api_design") or "").strip()
    history_lines: list[str] = []
    for msg in list(svc.get("messages") or [])[-6:]:
        role = str(msg.get("role") or "assistant")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        cap = 700 if role == "user" else 350
        history_lines.append(f"{role}: {content[:cap]}")
    parts = [
        f"Focus microservice: {name}",
        f"Role: {svc.get('role_key') or ''}",
        f"Peer microservices (call them; do not plan them): {', '.join(peers) or '(none)'}",
        f"Architect sketch for {name} only:\n{section or '(none)'}",
        f"Architect communication schemes (context only — do not lock protocols):\n{comms or '(none)'}",
        f"Agreed entity relationships:\n{relation_artifact(svc) or spec or '(not yet agreed)'}",
        f"Agreed features / functionality:\n{features or '(not yet agreed)'}",
        f"Current bugs:\n{str(svc.get('bug_spec') or '').strip() or '(none)'}",
        "Recent tile conversation:\n" + ("\n".join(history_lines) if history_lines else "(none)"),
        f"Latest user message:\n{pending or '(none)'}",
    ]
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(parts)
