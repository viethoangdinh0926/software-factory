"""Shared LLM helpers for principal-architect graph nodes."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from architect_agent.context_budget import (
    EXPLANATION_DEPTH_DIGEST,
    consult_user_turn,
    format_phase_context,
    refresh_discussion_digest,
)
from architect_agent.json_util import (
    parse_llm_json_object,
    recover_architecture_payload_from_prose,
)
from architect_agent.llm import get_chat_model
from architect_agent.query_intent import (
    SUGGESTED_ANSWER_RULES,
    USER_MESSAGE_FIRST_RULES,
    extract_http_endpoints,
    format_agreed_endpoints,
    is_revision_request,
    wants_endpoint_list,
    without_user_echo,
    with_resolution_close,
)

logger = logging.getLogger(__name__)

ProseRecover = Callable[[str], dict[str, Any] | None]

_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object. FIRST non-whitespace character must be `{`.\n"
    "No markdown, no ``` fences, no bare Mermaid.\n"
    "Fill THIS STEP's primary artifact in full (never empty). Other large fields \"\".\n"
    "Mermaid only in design_diagram_lines as short strings.\n"
    "assistant_message: keep the full elaborated justification (what changed, why, "
    "alternatives rejected, trade-offs accepted); escape its newlines as \\n. "
    "Ask them to confirm, approve, or agree — never tell them to click a button."
)

_QA_RETRY_HINT = (
    "CRITICAL FORMAT ERROR. Your previous reply was NOT valid JSON.\n"
    "Respond with ONE JSON object starting with `{`.\n"
    '{"assistant_message": "<full answer to the user question>"}\n'
    "Do not ask them to confirm in place of the answer. Do not rewrite artifacts."
)


def invoke_json(
    system: str,
    user: str,
    *,
    recover_prose: ProseRecover | None = None,
    prefer_prose: bool = False,
    retry_hint: str | None = None,
) -> dict[str, Any]:
    """Invoke the chat model and parse a JSON object, with retry + prose fallback."""
    model = get_chat_model()
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    content = _invoke_content(model, messages)
    try:
        return parse_llm_json_object(content)
    except ValueError as first_exc:
        if prefer_prose and recover_prose is not None:
            recovered_pref = recover_prose(content)
            if recovered_pref is not None:
                logger.warning(
                    "LLM JSON parse failed; using caller prose recovery: %s",
                    first_exc,
                )
                return recovered_pref

        recovered = recover_architecture_payload_from_prose(content)
        if recovered is not None and recovered.get("design_diagram_lines"):
            logger.warning(
                "LLM JSON parse failed; recovered Mermaid from prose: %s",
                first_exc,
            )
            return recovered

        logger.warning("LLM JSON parse failed; retrying once: %s", first_exc)
        retry_messages = [
            SystemMessage(content=system),
            HumanMessage(content=user),
            SystemMessage(content=retry_hint or _RETRY_HINT),
            HumanMessage(
                content=(
                    "Retry now. Output MUST start with `{`. Preview of your bad reply "
                    f"(truncated):\n{content[:900]}"
                )
            ),
        ]
        content2 = _invoke_content(model, retry_messages)
        try:
            return parse_llm_json_object(content2)
        except ValueError as second_exc:
            for blob in (content2, content):
                if recover_prose is not None:
                    recovered_custom = recover_prose(blob)
                    if recovered_custom is not None:
                        logger.warning(
                            "LLM JSON parse failed after retry; using caller prose recovery: %s",
                            second_exc,
                        )
                        return recovered_custom
            recovered2 = recover_architecture_payload_from_prose(content2)
            if recovered2 is not None:
                logger.warning(
                    "LLM JSON parse failed after retry; using prose recovery: %s",
                    second_exc,
                )
                return recovered2
            if recovered is not None:
                return recovered
            raise ValueError(
                f"LLM JSON parse failed after retry: {second_exc}"
            ) from first_exc


def _invoke_content(model: Any, messages: list[Any]) -> str:
    response = model.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def _qa_recover(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    return {"assistant_message": body}


def _artifacts_for_qa(state: dict[str, Any]) -> str:
    parts = [
        f"Phase: {state.get('phase') or ''}",
        f"Track: {state.get('design_track') or ''} step {state.get('design_step') or 0}",
        f"Business spec:\n{(state.get('business_spec') or '')[:6000]}",
        f"Trade-off ledger:\n{(state.get('tradeoff_ledger') or '')[:2500]}",
        f"Scale estimates:\n{(state.get('scale_estimates') or '')[:2500]}",
        f"Core microservices:\n{(state.get('api_contracts') or '')[:6000]}",
        f"Communication schemes:\n{(state.get('communication_schemes') or '')[:4000]}",
        f"FMEA notes:\n{(state.get('fmea_notes') or '')[:2500]}",
        f"Discussion memory:\n{(state.get('discussion_digest') or '')[:4000]}",
        f"Design diagram:\n{(state.get('design_diagram') or '')[:3000]}",
        f"Design justification:\n{(state.get('design_justification') or '')[:2500]}",
        f"Market grade: {state.get('market_evaluation_grade') or '(none)'}",
        f"Market report:\n{(state.get('market_evaluation_report') or '')[:4000]}",
    ]
    return "\n\n".join(parts)


def _last_assistant_text(state: dict[str, Any]) -> str:
    last = str(state.get("pending_assistant_message") or "").strip()
    if last:
        return last
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            text = str(msg.get("content") or "").strip()
            if text:
                return text
    return ""


def _open_questions_for_suggestions(state: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    interview = state.get("interview_questions") or []
    answers = state.get("interview_answers") or {}
    try:
        idx = int(state.get("current_question_index") or 0)
    except (TypeError, ValueError):
        idx = 0
    if isinstance(interview, list):
        for i, item in enumerate(interview):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            qid = str(item.get("id") or f"q{i}")
            already = str(answers.get(qid) or "").strip()
            if i >= idx or not already:
                questions.append(text)
    if questions:
        return questions[:8]
    last = _last_assistant_text(state)
    for line in last.splitlines():
        stripped = line.strip()
        if "?" in stripped or stripped.startswith("❓"):
            questions.append(stripped.lstrip("❓-• ").strip())
    return questions[:8]


def _suggested_answer_context(state: dict[str, Any]) -> str:
    last = _last_assistant_text(state)
    open_qs = _open_questions_for_suggestions(state)
    parts: list[str] = []
    if last:
        parts.append(f"Last assistant message (questions you asked):\n{last[:4000]}")
    if open_qs:
        parts.append(
            "Open questions to propose answers for:\n"
            + "\n".join(f"- {q}" for q in open_qs)
        )
    return "\n\n".join(parts)


def gate_user_chat(
    state: dict[str, Any],
    user_text: str,
    *,
    action: str,
    node: str,
    stay: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None]:
    """Consult the LLM on this isolated-chat reply.

    Returns (keynotes, kind, clarification_payload_or_None).
    If the payload is set, the wait node must return it and not proceed.
    """
    keynotes = str(state.get("discussion_digest") or "")
    if action != "chat" or not (user_text or "").strip():
        return keynotes, "", None
    last = _last_assistant_text(state)
    if not last.strip():
        return keynotes, "", None
    consult = consult_user_turn(
        pending=user_text,
        last_assistant=last,
        keynotes=keynotes,
        phase=node,
    )
    if consult.needs_clarification:
        msg = without_user_echo(
            (consult.clarify_message or "").strip()
            or (
                "Please address the open point in my previous message, or clarify "
                "what you meant, so I can continue this conversation."
            ),
            user_text,
        )
        payload = {
            **stay,
            "pending_user_feedback": "",
            "pending_assistant_message": msg,
            "stay_on_interrupt": True,
            "publish_requested": False,
            "discussion_digest": keynotes,
            "messages": [
                {"role": "user", "content": user_text, "node": node},
                {"role": "assistant", "content": msg, "node": node},
            ],
        }
        return keynotes, consult.kind, payload
    return consult.keynotes or keynotes, consult.kind, None


def answer_open_query(state: dict[str, Any], question: str, *, node: str) -> str:
    """Answer a user question from current artifacts without rewriting the step."""
    q = (question or "").strip()
    apis = str(state.get("api_contracts") or "")
    if wants_endpoint_list(q):
        return format_agreed_endpoints(
            extract_http_endpoints(apis, str(state.get("design_justification") or ""))
        )
    grade = str(state.get("market_evaluation_grade") or "").strip()
    if grade and "grade" in q.lower() and not is_revision_request(q):
        return f"The market evaluation grade for this design version is **{grade}**."
    extra_user = f"\n\n{_suggested_answer_context(state)}"
    history = format_phase_context(
        str(state.get("discussion_digest") or ""),
        [m for m in (state.get("messages") or []) if isinstance(m, dict) and m.get("node") == node],
        node,
    )
    result = invoke_json(
        system=(
            "Answer the user's question from the current artifacts. This is Q&A "
            "before they approve this workflow step.\n"
            f"Current node: {node}.\n"
            f"{USER_MESSAGE_FIRST_RULES}\n"
            f"{SUGGESTED_ANSWER_RULES}\n"
            "Use concrete facts (numbers, service names, owned objects, communication "
            "schemes/protocols, CAP choices).\n"
            f"{EXPLANATION_DEPTH_DIGEST}\n"
            "This turn is an ANSWER, not an artifact rewrite: go beyond stating the current "
            "value. Explain the reasoning behind it — the forces that drove it, the "
            "alternatives rejected, the trade-offs accepted — and name the relevant pattern "
            "or principle so the user leaves understanding the architecture.\n"
            "Do not ask them to confirm in place of the answer. Do not say you finalized "
            "or updated the design.\n"
            "If they raised a concern, address that concern directly — do not ignore it "
            "to restate the current step.\n"
            "Do not rewrite artifacts. assistant_message is the full answer.\n"
            'Respond ONLY with JSON: {"assistant_message": string}'
        ),
        user=(
            f"{history}\n\n{_artifacts_for_qa(state)}{extra_user}\n\n"
            f"Latest user message:\n{q}"
        ),
        recover_prose=_qa_recover,
        prefer_prose=True,
        retry_hint=_QA_RETRY_HINT,
    )
    return str(result.get("assistant_message") or "").strip() or (
        "I can answer from the current artifacts on this step. "
        "Ask about a specific section, or confirm, approve, or agree when you are ready to continue."
    )


def answer_before_approve(
    state: dict[str, Any],
    user_text: str,
    *,
    node: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Stay on the current wait interrupt after answering; keep Approve enabled."""
    answer = with_resolution_close(
        answer_open_query(state, user_text, node=node),
        changed=False,
    )
    digest = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending=user_text,
        assistant=answer,
        phase=str(state.get("phase") or node),
        track=str(state.get("design_track") or ""),
        spec=str(state.get("business_spec") or ""),
    )
    return {
        **base,
        "pending_user_feedback": "",
        "pending_assistant_message": answer,
        "stay_on_interrupt": True,
        "publish_requested": False,
        "discussion_digest": digest,
        "messages": [
            {"role": "user", "content": user_text, "node": node},
            {"role": "assistant", "content": answer, "node": node},
        ],
    }


APPROVE_LABELS = {
    ("phase0", 0): "Confirm scope & start track",
    ("lld", 1): "Confirm gathering → blueprint",
    ("lld", 2): "Confirm blueprint → verification",
    ("lld", 3): "Approve & send design",
    ("hld", 1): "Confirm requirements → domain model",
    ("hld", 2): "Confirm domain → microservices",
    ("hld", 3): "Confirm services → communication & diagram",
    ("hld", 4): "Confirm diagram → FMEA",
    ("hld", 5): "Confirm FMEA → synthesis",
    ("hld", 6): "Approve & send design",
    ("market_research", 0): "Continue after market evaluation",
}

HLD_STEP_TITLES = {
    1: "Requirements & capacity estimation",
    2: "Domain object modeling",
    3: "Core microservices",
    4: "Communication schemes, infrastructure & system diagram",
    5: "Vulnerability & edge-case analysis (FMEA)",
    6: "Session synthesis & wrap-up",
}

LLD_STEP_TITLES = {
    1: "Information gathering",
    2: "Architectural blueprint",
    3: "Verification",
}


def design_step_title(phase: str, track: str, step: int) -> str:
    if phase == "market_research":
        return "Market evaluation"
    if phase == "phase0" or track == "unset":
        return "Scope classification"
    if track == "lld":
        return LLD_STEP_TITLES.get(step, "Low-level design")
    if track == "hld":
        return HLD_STEP_TITLES.get(step, "High-level design")
    return ""


def approve_label(phase: str, track: str, step: int, *, design_ready: bool = False) -> str:
    if phase == "market_research":
        return APPROVE_LABELS[("market_research", 0)]
    if phase == "lld":
        return APPROVE_LABELS.get(("lld", step), "Continue")
    if phase == "hld":
        return APPROVE_LABELS.get(("hld", step), "Continue")
    if phase == "phase0":
        return APPROVE_LABELS[("phase0", 0)]
    if design_ready:
        return "Approve & send design"
    return "Continue"


def is_design_approve_step(track: str, step: int) -> bool:
    return (track == "lld" and step >= 3) or (track == "hld" and step >= 6)


_THIN_STATUS_RE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:lld|hld)?\s*step\s+\d+\s+(?:update|complete|done|ready)\.?"
    r"|design updated\.?"
    r"|here is the diagram\.?"
    r"|step\s+\d+\s+complete\.?"
    r")\s*$"
)

_NEXT_STEP_HINT = {
    ("lld", 1): "Next we draw the class/structure blueprint from these rules.",
    ("lld", 2): "Next we verify the blueprint against the spec invariants.",
    ("lld", 3): "If this looks right, confirm, approve, or agree so we can run market evaluation and hand the package off.",
    ("hld", 1): "Next we model the domain objects those numbers imply.",
    ("hld", 2): "Next we split owned objects into core microservices.",
    ("hld", 3): "Next we name communication schemes and draw the system diagram.",
    ("hld", 4): "Next we run FMEA against this topology.",
    ("hld", 5): "Next we synthesize the session and wrap up.",
    ("hld", 6): "If this looks right, confirm, approve, or agree so we can run market evaluation and hand the package off.",
}


def assistant_message_is_thin(text: str) -> bool:
    """True when chat is a status line, not a briefing of what this step completed."""
    body = (text or "").strip()
    if not body:
        return True
    if _THIN_STATUS_RE.match(body):
        return True
    lower = body.lower()
    if (
        "need more information to proceed" in lower
        or "provide more details about your system" in lower
        or "please share more detail" in lower
    ):
        return True
    # Strip the system next-action footer if a caller already appended it.
    core = re.split(r"\n\*\*What you can do next\*\*", body, maxsplit=1)[0].strip()
    core = re.split(r"\n\*\*Updates to this proposal\*\*", core, maxsplit=1)[0].strip()
    words = core.split()
    if len(words) < 28:
        return True
    return False


def _primary_artifact_body(artifacts: dict[str, str], primary_field: str) -> str:
    """Full step artifact for chat — never a mid-word or mid-markup clip."""
    body = (artifacts.get(primary_field) or "").strip()
    if body:
        return body
    for key in (
        "scale_estimates",
        "api_contracts",
        "communication_schemes",
        "fmea_notes",
        "design_justification",
        "business_spec",
        "tradeoff_ledger",
        "design_diagram",
    ):
        body = (artifacts.get(key) or "").strip()
        if body:
            return body
    return "(artifact captured on this step)"


def synthesize_step_briefing(
    *,
    track: str,
    step: int,
    title: str,
    artifacts: dict[str, str],
    primary_field: str,
    pending: str = "",
) -> str:
    """Explain what this LLD/HLD step produced, using the full step artifact."""
    del pending
    label = track.upper()
    body = _primary_artifact_body(artifacts, primary_field)
    extra = ""
    diagram = artifacts.get("design_diagram") or ""
    if track == "lld" and step >= 2 and diagram.strip():
        nodes = len(set(re.findall(r"\b([A-Za-z][\w]*)\s*(?:\[|\(|\{)", diagram)))
        extra = f"\n\nThe class/structure diagram now has about **{nodes or 'several'}** named nodes."
    if track == "hld" and step == 4 and diagram.strip():
        nodes = len(set(re.findall(r"\b([A-Za-z][\w]*)\s*(?:\[|\(|\{)", diagram)))
        extra = f"\n\nThe system diagram now has about **{nodes or 'several'}** named nodes (clients, gateway, services, stores)."
    nxt = _NEXT_STEP_HINT.get((track, step), "If this looks right, confirm, approve, or agree so we can continue.")
    return (
        f"**{label} step {step} — {title}** is complete enough to review.\n\n"
        f"Here is what this step locked in:\n\n{body}{extra}\n\n"
        f"{nxt}"
    )


def ensure_step_briefing(
    message: str,
    *,
    track: str,
    step: int,
    title: str,
    artifacts: dict[str, str],
    primary_field: str,
    pending: str = "",
) -> str:
    """Replace empty/status-line chat with a briefing of what the step completed."""
    raw = without_user_echo((message or "").strip(), pending)
    if not assistant_message_is_thin(raw):
        return raw
    return synthesize_step_briefing(
        track=track,
        step=step,
        title=title,
        artifacts=artifacts,
        primary_field=primary_field,
        pending=pending,
    )
