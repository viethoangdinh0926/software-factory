"""Shared LLM helpers and service-list utilities."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from orchestrator_agent.json_util import parse_llm_json_object
from orchestrator_agent.llm import get_chat_model
from orchestrator_agent.query_intent import with_resolution_close

ProseRecover = Callable[[str], dict[str, Any] | None]

logger = logging.getLogger(__name__)

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`.\n"
    "Keep assistant_message's full elaborated justification; escape its newlines as \\n."
)

# Appended to skill_digest() so every orchestrator prompt inherits the same depth bar:
# user-visible messages must justify decisions, not just announce them.
EXPLANATION_DEPTH_DIGEST = """
CHAT DEPTH (assistant_message) — write like a Staff Engineer briefing the team:
- Target 200-500 words of substance. NEVER a bare status line such as "Plan updated.",
  "Tech stack selected." or "Extracted the services." A recap with no reasoning is a
  FAILED turn.
- Every recommendation you surface (topology, features, API type, API design, tech stack,
  plan spec, service split) must justify itself by covering:
  1. WHAT you decided, naming concrete elements — service names, METHOD /path endpoints,
     libraries with their role, data stores — not "the stack" or "the design".
  2. WHY it fits THIS service/system: the driving forces from the architect package
     (contract shape, traffic profile, latency budget, consistency need, team skills).
  3. ALTERNATIVES considered and the explicit reason each was rejected (e.g. "gRPC
     rejected: this is a browser-facing edge API, so REST+JSON avoids a proxy layer").
  4. TRADE-OFFS accepted — operational cost, added latency, lock-in, learning curve —
     and why they are acceptable here.
  5. ASSUMPTIONS made where the package was silent, each labeled with your default.
  6. IMPLICATIONS for the downstream engineer implementing this.
- Teach while you decide: name the pattern or principle you are applying (idempotency
  keys, outbox, saga, circuit breaker, CQRS, backpressure, twelve-factor) and say in one
  clause what it buys here. Prefer concrete versions/numbers over vague adjectives.
- Use tight markdown structure (bold lead-ins, short bullets) so it stays scannable.
  Depth means information density, NOT padding — no filler, no restating the question.
- Still invite Approve once the artifact is ready. Elaboration replaces terseness; it
  does not replace the approve flow.
""".strip()

APPROVE_LABELS = {
    "confirm_topology": "Confirm topology",
    "approve_features": "Approve features",
    "decide_api_type": "Accept API type",
    "approve_api_design": "Approve API design",
    "approve_plan": "Approve plan",
}

STATUS_APPROVE_KIND = {
    "awaiting_features": "approve_features",
    "awaiting_api_type": "decide_api_type",
    "awaiting_api_design": "approve_api_design",
    "awaiting_stack": "approve_plan",
}


def features_are_concrete(text: str) -> bool:
    """Require a real v1 feature list, not a one-line sketch."""
    body = (text or "").strip()
    if len(body) < 280:
        return False
    bullets = len(re.findall(r"(?m)^\s*[-*]", body))
    numbered = len(re.findall(r"(?m)^\s*\d+\.", body))
    return (bullets + numbered) >= 4


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
    out["approve_kind"] = kind if out["can_approve"] else ""
    out["approve_label"] = approve_label(kind) if out["can_approve"] else ""
    out["discussion_open"] = open_disc
    return out


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
    data = payload or {}
    for key in ("assistant_message", "rationale", "summary", "message"):
        text = _as_visible_text(data.get(key))
        if text:
            return text
    for key in artifact_keys:
        text = _as_visible_text(data.get(key))
        if not text:
            continue
        if len(text) <= preview_limit:
            return text
        clipped = text[:preview_limit]
        if "\n" in clipped:
            clipped = clipped.rsplit("\n", 1)[0]
        return clipped.rstrip() + "\n\n…"
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
            "Do not invite Approve. Do not say you finalized or updated anything.\n"
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
        text = path.read_text(encoding="utf-8")[:4000]
    except OSError:
        text = "You are the Software Factory Orchestrator."
    return f"{text}\n\n{EXPLANATION_DEPTH_DIGEST}"


def service_focus_system(name: str) -> str:
    """System prefix so a planning tile stays on one microservice."""
    return (
        f"You are planning ONE microservice: {name}. This tile is not the platform design.\n"
        f"Stay on {name}'s features, API, behavior, and its own data/runtime.\n"
        "Do not restate overall architecture. Do not design other microservices.\n"
        "Do not prescribe shared infra (CDN, load balancers, Kafka, Redis, object storage, "
        "search clusters) unless this service itself owns that store or must call it as a client.\n"
        "Peer services may be invoked; do not specify their internals or tech choices.\n"
        "If the user asked a question, answer it in assistant_message with concrete facts "
        "(methods, paths, fields). Do not reply with a status recap like 'I finalized the design'. "
        "If they raised a concern or asked to change something, update this service's artifact, "
        "list **Updates to this proposal**, then invite Approve for that version."
    )


def service_focus_user_block(
    state: dict[str, Any],
    svc: dict[str, Any],
    *,
    pending: str = "",
    extra: str = "",
) -> str:
    """User payload with this service's contract, peers, and tile chat — not the full package."""
    from orchestrator_agent.package_parse import service_contract_section

    names = [str(n) for n in (svc.get("names") or []) if n]
    name = names[-1] if names else "Service"
    peers = [
        str((s.get("names") or ["peer"])[-1])
        for s in (state.get("services") or [])
        if s.get("status") != "suspended" and s.get("microservice_id") != svc.get("microservice_id")
    ]
    contract = str(svc.get("architect_api_contract") or "").strip()
    features = str(svc.get("feature_spec") or "").strip()
    section = service_contract_section(str(state.get("package_markdown") or ""), names) or contract
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
        f"Agreed features / functionality:\n{features or '(not yet agreed)'}",
        "Recent tile conversation:\n" + ("\n".join(history_lines) if history_lines else "(none)"),
        f"Latest user message:\n{pending or '(none)'}",
    ]
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(parts)
