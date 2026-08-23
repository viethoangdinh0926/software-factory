from __future__ import annotations

import json
from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    TRACK_CLASSIFICATION_RULES,
    format_phase_context,
    maybe_compact_business_spec,
    refresh_discussion_digest,
)
from architect_agent.design_progress import rewind_or_block_skip, with_rewind_notice
from architect_agent.graph.nodes.common import answer_before_approve, approve_label, gate_user_chat, invoke_json
from architect_agent.graph.state import DesignGraphState
from architect_agent.interview_progress import (
    append_spec_bullet,
    ensure_specific_question,
    extract_question_titles,
    guess_spec_section,
    living_spec_scaffold,
    record_dropped_constraints,
    specific_followup_message,
)
from architect_agent.json_util import recover_interview_payload_from_prose
from architect_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    USER_MESSAGE_FIRST_RULES,
    is_advance_request,
    is_informational_query,
    is_revision_request,
    is_step_approval_message,
    promote_chat_to_approve,
    user_message_first_block,
    without_user_echo,
    with_next_prompt,
    with_resolution_close,
)
from architect_agent.scope import (
    ensure_classified_topology,
    ensure_standalone_spec,
    recommend_design_track,
    resolve_design_track,
    track_reclass_notice,
    wants_standalone,
)
from architect_agent.web_search import perform_web_search

_STRUCTURED_SPEC_HINTS = (
    "## actors",
    "## in scope",
    "## out of scope",
    "## goals",
    "## problem",
    "## critical invariants",
    "## success criteria",
    "## assumptions",
)


def _spec_looks_structured(text: str) -> bool:
    """True when the living spec already has enough signal to classify LLD vs HLD."""
    body = text or ""
    lower = body.lower()
    if any(
        k in lower
        for k in (
            "microservice",
            "distributed",
            "single os process",
            "single process",
            "in-process",
            "stand-alone",
            "standalone",
            "self-contained",
            "(lld)",
            "(hld)",
        )
    ):
        return True
    if "##" not in body:
        return False
    if len(body) > 500:
        return True
    return sum(1 for hint in _STRUCTURED_SPEC_HINTS if hint in lower) >= 3


def _phase_context(state: DesignGraphState) -> str:
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
    return format_phase_context(str(state.get("discussion_digest") or ""), prior, "phase0")


def _remember(state: DesignGraphState, out: dict[str, Any], pending: str) -> dict[str, Any]:
    assistant = str(out.get("pending_assistant_message") or "")
    spec = str(out.get("business_spec") or state.get("business_spec") or "")
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
    assistant = ensure_specific_question(
        assistant,
        spec=spec,
        pending=pending,
        asked_titles=extract_question_titles(prior),
    )
    out["pending_assistant_message"] = assistant
    msgs = out.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[-1], dict):
        if msgs[-1].get("role") == "assistant":
            msgs[-1] = {**msgs[-1], "content": assistant}
    out["discussion_digest"] = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending=pending,
        assistant=assistant,
        phase=str(out.get("phase") or "phase0"),
        track=str(out.get("design_track") or state.get("design_track") or "unset"),
        spec=spec,
    )
    return out


def _lock_track(
    state: DesignGraphState,
    llm_track: str,
    pending: str,
    spec: str,
    *,
    require_track: bool = False,
) -> tuple[str, str]:
    """Return (track, optional user-facing reclass note)."""
    prior = str(state.get("design_track") or "unset").lower()
    new_track = resolve_design_track(llm_track, pending=pending, spec=spec, prior=prior)
    if require_track and new_track not in {"lld", "hld"}:
        new_track = recommend_design_track(pending=pending, spec=spec, prior=prior)
    return new_track, track_reclass_notice(prior, new_track)


_SPEC_APPROVE_TAIL = (
    "If this specification looks right, confirm, approve, or agree so I can classify "
    "LLD vs HLD. Otherwise tell me what to add or change."
)
_TRACK_APPROVE_TAIL = (
    "If this looks right, confirm, approve, or agree so we can begin the track. "
    "Otherwise tell me what to change."
)


def _message_includes_spec(message: str, spec: str) -> bool:
    body = (spec or "").strip()
    if not body:
        return True
    sample = body[:180].strip()
    if not sample:
        return True
    return sample in (message or "")


def _present_compiled_spec(compiled_spec: str, llm_message: str) -> str:
    spec = (compiled_spec or "").strip()
    body = (llm_message or "").strip()
    if spec and not _message_includes_spec(body, spec):
        intro = body or "Here is the compiled project specification."
        body = f"{intro}\n\n{spec}"
    if "confirm, approve, or agree" not in body.lower():
        body = f"{body}\n\n{_SPEC_APPROVE_TAIL}".strip()
    return body


def _present_classification(assistant: str, track: str) -> str:
    chosen = track if track in {"lld", "hld"} else "hld"
    body = (assistant or "").strip()
    if "unset" in body.lower():
        body = body.replace("UNSET", chosen.upper()).replace("unset", chosen.upper())
    if f"**{chosen.upper()}**" not in body and chosen.upper() not in body:
        prefix = f"Scope classified as **{chosen.upper()}**."
        body = f"{prefix}\n\n{body}".strip() if body else prefix
    if "confirm, approve, or agree" not in body.lower():
        body = f"{body}\n\n{_TRACK_APPROVE_TAIL}".strip()
    return body


def _maybe_resolution_close(assistant: str, pending: str, *, changed: bool) -> str:
    if not pending or is_step_approval_message(pending):
        return assistant
    return with_resolution_close(assistant, changed=changed)


def _conversation_already_started(state: DesignGraphState) -> bool:
    """True once Phase 0 has already spoken — later pending is a reply, not discovery."""
    return any(
        isinstance(m, dict) and m.get("role") == "assistant" and m.get("node") == "phase0"
        for m in (state.get("messages") or [])
    )


def _fold_decision_into_spec(spec: str, pending: str) -> str:
    """Merge this turn's decision into the living spec so the artifact updates live."""
    living = living_spec_scaffold(spec)
    pending_text = (pending or "").strip()
    if not pending_text:
        return living
    if is_step_approval_message(pending_text) and len(pending_text.split()) <= 4:
        return ensure_standalone_spec(living, pending_text)
    fold = invoke_json(
        system=(
            "Update the living business specification markdown from one interview answer.\n"
            f"{user_message_first_block(pending_text)}"
            "Merge the answer into the correct sections (Actors, Goals, In scope, "
            "Out of scope, Deployment topology, Critical invariants, Success criteria, "
            "Assumptions). Keep the document concise.\n"
            "Do NOT append raw Q&A, interview transcripts, or growing 'notes' logs.\n"
            "Prefer rewriting bullets over adding new narrative paragraphs.\n"
            "Keep every prior decision that is still valid.\n"
            'Return JSON: {"updated_business_spec": string}.'
        ),
        user=f"Spec:\n{living}\n\nLatest user message:\n{pending_text}\n",
        recover_prose=recover_interview_payload_from_prose,
        prefer_prose=True,
    )
    updated = str(fold.get("updated_business_spec") or "").strip() or living
    lost_structure = living.lower().count("##") >= 3 and updated.lower().count("##") < 3
    shrunk = living.lower().count("##") >= 3 and len(updated) + 80 < len(living)
    if updated == living or "##" not in updated or lost_structure or shrunk:
        updated = append_spec_bullet(living, guess_spec_section(pending_text), pending_text[:400])
    updated = ensure_standalone_spec(updated, pending_text)
    updated = record_dropped_constraints(updated, pending_text)
    return maybe_compact_business_spec(updated)


def _compile_phase0_spec(
    state: DesignGraphState,
    *,
    pending: str,
    business_spec: str,
    interview_questions: list[Any],
    interview_answers: dict[str, Any],
    history_tail: str,
) -> dict[str, Any]:
    """Compile the living spec, show it, and ask for approval or updates."""
    spec_compilation_result = invoke_json(
        system=(
            "You are the Architect agent's Phase 0 spec compiler (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            "Task: Compile a comprehensive business specification from interview answers.\n"
            "Use the questions and answers to create a well-structured spec.\n"
            "Include sections like: Executive Summary, Core Business Requirements, "
            "Non-Functional Requirements, Technical Constraints, Success Metrics, etc.\n"
            "Include a ## Deployment topology section (local stand-alone / LLD vs "
            "distributed / HLD) with an explicit recommendation.\n"
            "Honor DISCUSSION MEMORY: do not drop settled issues or rejected proposals.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "compiled_spec": string,\n'
            '  "assistant_message": string\n'
            "}\n"
            "assistant_message MUST include the full compiled spec in markdown and ask "
            "the user to confirm, approve, or agree — or tell you what to add or change. "
            "Never tell them you merely have enough to compile without showing the spec. "
            "Never tell them to click a button."
        ),
        user=(
            f"Original request:\n\n{business_spec}\n\n"
            f"{history_tail}\n\n"
            f"Interview questions and answers:\n"
            f"{json.dumps({'questions': interview_questions, 'answers': interview_answers}, indent=2)}\n\n"
            f"Latest user message:\n{pending or '(none)'}\n"
        ),
    )

    compiled_spec = ensure_standalone_spec(
        spec_compilation_result.get("compiled_spec") or business_spec,
        " ".join(str(v) for v in (interview_answers or {}).values()),
    )
    assistant = _present_compiled_spec(
        compiled_spec,
        without_user_echo(
            str(spec_compilation_result.get("assistant_message") or "").strip(),
            pending,
        ),
    )
    assistant = _maybe_resolution_close(
        assistant,
        pending,
        changed=compiled_spec != business_spec or bool(pending),
    )
    questions = interview_questions if isinstance(interview_questions, list) else []
    try:
        next_index = len(questions)
    except TypeError:
        next_index = 0
    return _remember(
        state,
        {
            "phase": "phase0",
            "design_track": "unset",
            "design_step": 0,
            "business_spec": compiled_spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": True,
            "ready_for_design": False,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "interview_questions": questions,
            "interview_answers": interview_answers if isinstance(interview_answers, dict) else {},
            "current_question_index": next_index,
            "interview_complete": True,
            "spec_compiled": True,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
        },
        pending,
    )


def _conduct_phase0_turn(
    state: DesignGraphState,
    *,
    pending: str,
    business_spec: str,
    history_tail: str,
    interview_questions: list[Any],
    current_question_index: int,
    interview_answers: dict[str, Any],
) -> dict[str, Any]:
    """Reply to the user turn: fold into the spec, then at most one next question."""
    questions_in = [q for q in (interview_questions or []) if isinstance(q, dict)]
    try:
        idx = int(current_question_index or 0)
    except (TypeError, ValueError):
        idx = 0
    current_question: dict[str, Any] = {}
    if questions_in and 0 <= idx < len(questions_in):
        current_question = questions_in[idx]
    question_id = str(current_question.get("id") or (f"q{idx}" if current_question else ""))
    answers = dict(interview_answers or {})
    if question_id:
        answers[question_id] = pending
    spec = _fold_decision_into_spec(business_spec, pending)
    turn = invoke_json(
        system=(
            "You are the Architect agent's Phase 0 interview conductor (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            f"{USER_MESSAGE_FIRST_RULES}\n"
            "The question list is a checklist, not a script. Address the user's "
            "message first. The living spec was already updated from this message — "
            "do not drop those decisions. Focus assistant_message on acknowledging "
            "the decision and asking at most one next specific question.\n"
            "You may stay on the current question, skip ahead, rewrite a later "
            "question, or mark the interview complete — but never reply with only "
            "the next canned question.\n"
            "If the checklist is empty, still reply to Latest user message. Do not "
            "introduce a brand-new interview as if this were the first turn.\n"
            "Honor DISCUSSION MEMORY: never re-ask a settled issue or re-suggest a "
            "rejected solution. If they confirm ignoring or dropping a concern you "
            "raised (sync, security, etc.), LOCK it in the spec in one sentence and "
            "ask the next uncovered specific question — do not rebut it again.\n"
            f"{TRACK_CLASSIFICATION_RULES}\n"
            "If the user wants a local stand-alone app, lock that in the spec and do "
            "not ask about CDN, multi-region, or microservices.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "updated_business_spec": string,\n'
            '  "questions": [{"id": string, "text": string, "category": string}],\n'
            '  "current_question_index": number,\n'
            '  "interview_complete": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
            "updated_business_spec: return the current living spec (already folded); "
            "only edit it if you must add a missing section. Never empty it.\n"
            "questions: return the full list (revised if needed). "
            "current_question_index: index of the NEXT question to ask, or "
            "len(questions) if complete.\n"
            f"{FEEDBACK_RESOLUTION_RULES}"
        ),
        user=(
            f"Current living specification:\n\n{spec}\n\n"
            f"Interview checklist:\n{json.dumps(questions_in, indent=2)}\n\n"
            f"Answers so far:\n{json.dumps(answers, indent=2)}\n\n"
            f"Current question index: {idx}\n"
            "Current question: "
            f"{current_question.get('text') or '(none — reply to the user; do not mint a fresh interview script)'}\n\n"
            f"{history_tail}\n\n"
            f"Latest user message:\n{pending}\n"
        ),
    )
    questions = turn.get("questions") or questions_in
    if not isinstance(questions, list) or not questions:
        questions = questions_in
    try:
        next_index = int(turn.get("current_question_index"))
    except (TypeError, ValueError):
        next_index = idx + 1 if questions_in else 0
    next_index = max(0, min(next_index, len(questions)))
    complete = bool(turn.get("interview_complete")) or (
        bool(questions) and next_index >= len(questions)
    )
    if complete:
        return _compile_phase0_spec(
            state,
            pending=pending,
            business_spec=spec,
            interview_questions=questions,
            interview_answers=answers,
            history_tail=history_tail,
        )
    assistant = without_user_echo(str(turn.get("assistant_message") or "").strip(), pending)
    if not assistant:
        follow, _ready = specific_followup_message(
            spec,
            pending,
            extract_question_titles(
                [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
            ),
        )
        assistant = follow
    if wants_standalone(pending):
        assistant = (
            "Locked **local self-contained stand-alone** (LLD). I will not keep asking "
            "about distributed, CDN, or microservice topology.\n\n"
            + assistant
        )
    assistant = with_resolution_close(assistant, changed=spec != business_spec or bool(pending))
    return _remember(
        state,
        {
            "phase": "phase0",
            "design_track": "unset",
            "design_step": 0,
            "business_spec": spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": False,
            "ready_for_design": False,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "interview_questions": questions,
            "interview_answers": answers,
            "current_question_index": next_index,
            "interview_complete": False,
            "spec_compiled": False,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
        },
        pending,
    )


def phase0_classify_node(state: DesignGraphState) -> dict[str, Any]:
    """Phase 0: interactive question-based interview to gather comprehensive requirements."""
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    history_tail = _phase_context(state)
    track = state.get("design_track") or "unset"
    interview_questions = list(state.get("interview_questions") or [])
    current_question_index = int(state.get("current_question_index") or 0)
    interview_answers = dict(state.get("interview_answers") or {})
    interview_complete = bool(state.get("interview_complete", False))
    spec_compiled = bool(state.get("spec_compiled", False))
    if (
        interview_questions
        and current_question_index >= len(interview_questions)
        and not spec_compiled
    ):
        interview_complete = True
    
    # If we have a compiled spec and user wants to proceed with classification
    if spec_compiled and interview_complete:
        # Concerns / edits after the compiled spec must be applied, not skipped.
        if pending and is_revision_request(pending) and not is_step_approval_message(pending):
            spec_update_result = invoke_json(
                system=(
                    "You are the Architect agent's Phase 0 spec refiner (Principal Architect).\n"
                    f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                    f"{JSON_OUTPUT_DIGEST}\n\n"
                    f"{user_message_first_block(pending)}"
                    "Task: Update the business specification based on the user's comments.\n"
                    "Address every concern in assistant_message, then integrate requested "
                    "changes into the spec.\n"
                    "After updating, classify as LLD vs HLD — never unset — and set "
                    "ready_to_advance=true.\n"
                    f"{TRACK_CLASSIFICATION_RULES}\n"
                    "Honor DISCUSSION MEMORY: do not re-open settled issues or re-suggest "
                    "rejected solutions.\n"
                    "If the user asked for a local/self-contained/stand-alone app, set "
                    "design_track=lld and record that under ## Deployment topology.\n"
                    "Respond ONLY with JSON:\n"
                    "{\n"
                    '  "updated_business_spec": string,\n'
                    '  "design_track": "lld" | "hld",\n'
                    '  "ready_to_advance": boolean,\n'
                    '  "assistant_message": string\n'
                    "}\n"
                ),
                user=(
                    f"Current living specification:\n\n{business_spec}\n\n"
                    f"{history_tail}\n\n"
                    f"Latest user message:\n{pending}\n"
                ),
            )

            new_track = str(spec_update_result.get("design_track") or "unset").lower()
            if new_track not in {"unset", "lld", "hld"}:
                new_track = "unset"
            spec = ensure_standalone_spec(
                spec_update_result.get("updated_business_spec") or business_spec,
                pending,
            )
            new_track, reclass_note = _lock_track(
                state, new_track, pending, spec, require_track=True
            )
            spec = ensure_classified_topology(spec, new_track)
            ready = new_track in {"lld", "hld"}
            assistant = without_user_echo(
                str(spec_update_result.get("assistant_message") or ""), pending
            )
            if reclass_note:
                assistant = f"{reclass_note}\n\n{assistant}" if assistant else reclass_note
            assistant = _present_classification(
                assistant or "Updated the specification from your comments.",
                new_track,
            )
            assistant = _maybe_resolution_close(
                assistant, pending, changed=spec != business_spec
            )
            assistant = with_rewind_notice(assistant, str(state.get("rewind_notice") or ""))
            later_work = bool(
                int(state.get("rewalk_until_step") or 0)
                or state.get("scale_estimates")
                or state.get("api_contracts")
                or state.get("design_diagram")
                or state.get("communication_schemes")
                or state.get("fmea_notes")
            )
            if later_work:
                assistant = (
                    f"{assistant}\n\nLater-step design artifacts stay in place. "
                    "As we walk the track again I will patch them only where this spec change requires it."
                )

            return _remember(
                state,
                {
                "phase": "phase0",
                "design_track": new_track,  # type: ignore[typeddict-item]
                "design_step": 0,
                "business_spec": spec,
                "tradeoff_ledger": state.get("tradeoff_ledger") or "",
                "ready_to_advance": ready,
                "ready_for_design": ready,
                "design_ready_to_approve": False,
                "spec_enhanced": True,
                "spec_approved": True,
                "interview_complete": True,
                "spec_compiled": True,
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "rewind_notice": "",
                "carry_change": pending,
                "publish_requested": False,
                "stay_on_interrupt": False,
                "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
                },
                pending,
            )
        else:
            # User is satisfied with the spec, proceed to classification
            result = invoke_json(
                system=(
                    "You are the Architect agent's Phase 0 classifier (Principal Architect).\n"
                    f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                    f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
                    f"{JSON_OUTPUT_DIGEST}\n\n"
                    f"{user_message_first_block(pending)}"
                    "Task: classify LLD vs HLD from the compiled spec.\n"
                    f"{TRACK_CLASSIFICATION_RULES}\n"
                    "Honor DISCUSSION MEMORY: do not re-open settled issues.\n"
                    "Set design_track to lld or hld — never unset — and ready_to_advance=true.\n"
                    "assistant_message should say the classification and ask them to confirm, "
                    "approve, or agree to start the track. Never tell them to click a button.\n"
                    "Respond ONLY with JSON:\n"
                    "{\n"
                    '  "design_track": "lld" | "hld",\n'
                    '  "ready_to_advance": boolean,\n'
                    '  "updated_business_spec": string,\n'
                    '  "tradeoff_ledger": string,\n'
                    '  "assistant_message": string\n'
                    "}\n"
                    "updated_business_spec: apply any user comments; otherwise keep the spec.\n"
                    "tradeoff_ledger: one line noting the classification assumption.\n"
                    f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
                ),
                user=(
                    f"Current living specification:\n\n{business_spec}\n\n"
                    f"{history_tail}\n\n"
                    f"Latest user message:\n{pending or '(none — no changes requested)'}\n"
                ),
            )

            new_track = str(result.get("design_track") or "unset").lower()
            if new_track not in {"unset", "lld", "hld"}:
                new_track = "unset"
            spec = ensure_standalone_spec(
                result.get("updated_business_spec") or business_spec, pending
            )
            new_track, reclass_note = _lock_track(
                state, new_track, pending, spec, require_track=True
            )
            spec = ensure_classified_topology(spec, new_track)
            ready = new_track in {"lld", "hld"}
            ledger = result.get("tradeoff_ledger") or state.get("tradeoff_ledger") or ""
            assistant = without_user_echo(str(result.get("assistant_message") or ""), pending)
            if reclass_note:
                assistant = f"{reclass_note}\n\n{assistant}" if assistant else reclass_note
            assistant = _present_classification(assistant, new_track)
            assistant = _maybe_resolution_close(
                assistant,
                pending,
                changed=spec != business_spec
                or new_track != str(state.get("design_track") or "unset"),
            )
            assistant = with_rewind_notice(str(assistant), str(state.get("rewind_notice") or ""))

            return _remember(
                state,
                {
                "phase": "phase0",
                "design_track": new_track,  # type: ignore[typeddict-item]
                "design_step": 0,
                "business_spec": spec,
                "tradeoff_ledger": ledger,
                "ready_to_advance": ready,
                "ready_for_design": ready,
                "design_ready_to_approve": False,
                "spec_enhanced": True,
                "spec_approved": True,
                "interview_complete": True,
                "spec_compiled": True,
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "rewind_notice": "",
                "publish_requested": False,
                "stay_on_interrupt": False,
                "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
                },
                pending,
            )

    # If interview is complete but spec not yet compiled, compile the spec
    if interview_complete and not spec_compiled:
        return _compile_phase0_spec(
            state,
            pending=pending,
            business_spec=business_spec,
            interview_questions=interview_questions,
            interview_answers=interview_answers,
            history_tail=history_tail,
        )
    
    # If interview is in progress, ask the next question
    interview_questions = state.get("interview_questions", [])
    current_question_index = state.get("current_question_index", 0)
    interview_answers = state.get("interview_answers", {})
    
    # If we have interview questions, check if we need to ask the next one
    if interview_questions and current_question_index < len(interview_questions):
        if not pending:
            current_question = interview_questions[current_question_index]
            return _remember(
                state,
                {
                "phase": "phase0",
                "design_track": "unset",
                "design_step": 0,
                "business_spec": business_spec,
                "tradeoff_ledger": state.get("tradeoff_ledger") or "",
                "ready_to_advance": False,
                "ready_for_design": False,
                "design_ready_to_approve": False,
                "spec_enhanced": True,
                "interview_questions": interview_questions,
                "interview_answers": interview_answers,
                "current_question_index": current_question_index,
                "interview_complete": False,
                "spec_compiled": False,
                "pending_user_feedback": "",
                "pending_assistant_message": current_question.get("text", ""),
                "publish_requested": False,
                "stay_on_interrupt": False,
                "messages": [{"role": "assistant", "content": current_question.get("text", ""), "node": "phase0"}],
                },
                pending,
            )

        if is_informational_query(pending):
            return answer_before_approve(
                state,
                pending,
                node="phase0",
                base={
                    "phase": "phase0",
                    "design_track": "unset",
                    "design_step": 0,
                    "ready_to_advance": False,
                    "interview_questions": interview_questions,
                    "interview_answers": interview_answers,
                    "current_question_index": current_question_index,
                    "interview_complete": False,
                    "spec_compiled": False,
                },
            )

        return _conduct_phase0_turn(
            state,
            pending=pending,
            business_spec=business_spec,
            history_tail=history_tail,
            interview_questions=list(interview_questions or []),
            current_question_index=int(current_question_index or 0),
            interview_answers=dict(interview_answers or {}),
        )
    
    # If spec is already comprehensive, use original classification logic
    spec_comprehensive = _spec_looks_structured(business_spec)
    if spec_comprehensive:
        result = invoke_json(
            system=(
                "You are the Architect agent's Phase 0 classifier (Principal Architect).\n"
                f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
                f"{JSON_OUTPUT_DIGEST}\n\n"
                f"{user_message_first_block(pending)}"
                "Task: classify LLD vs HLD from the spec on THIS turn if possible.\n"
                f"{TRACK_CLASSIFICATION_RULES}\n"
                "Honor DISCUSSION MEMORY: do not re-open settled issues.\n"
                "Do NOT ask for DAU/QPS/bitrate here — that is HLD Step 1.\n"
                "If classification is already clear, set design_track to lld or hld — "
                "never unset — and ready_to_advance=true. assistant_message should say "
                "the classification and ask them to confirm, approve, or agree to start "
                "the track. Never tell them to click a button.\n"
                "Ask ONE clarifying question ONLY if LLD vs HLD is truly ambiguous; still "
                "propose a (Recommended) track and still set design_track to that pick.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "design_track": "lld" | "hld",\n'
                '  "ready_to_advance": boolean,\n'
                '  "updated_business_spec": string,\n'
                '  "tradeoff_ledger": string,\n'
                '  "assistant_message": string\n'
                "}\n"
                "updated_business_spec: keep/structure the spec; do not empty it.\n"
                "tradeoff_ledger: one line noting the classification assumption.\n"
                "Escape newlines in strings as \\n.\n"
                f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
            ),
            user=(
                f"Current living specification:\n\n{business_spec}\n\n"
                f"{history_tail}\n\n"
                f"Latest user message:\n{pending or '(none — initial classification)'}\n"
            ),
        )

        new_track = str(result.get("design_track") or "unset").lower()
        if new_track not in {"unset", "lld", "hld"}:
            new_track = "unset"
        spec = ensure_standalone_spec(
            result.get("updated_business_spec") or business_spec, pending
        )
        new_track, reclass_note = _lock_track(
            state, new_track, pending, spec, require_track=True
        )
        spec = ensure_classified_topology(spec, new_track)
        ready = new_track in {"lld", "hld"}
        ledger = result.get("tradeoff_ledger") or state.get("tradeoff_ledger") or ""
        assistant = without_user_echo(str(result.get("assistant_message") or ""), pending)
        if reclass_note:
            assistant = f"{reclass_note}\n\n{assistant}" if assistant else reclass_note
        assistant = _present_classification(assistant, new_track)
        assistant = _maybe_resolution_close(
            str(assistant),
            pending,
            changed=new_track != str(state.get("design_track") or "unset")
            or spec != business_spec,
        )
        assistant = with_rewind_notice(str(assistant), str(state.get("rewind_notice") or ""))

        return _remember(
            state,
            {
            "phase": "phase0",
            "design_track": new_track,  # type: ignore[typeddict-item]
            "design_step": 0,
            "business_spec": spec,
            "tradeoff_ledger": ledger,
            "ready_to_advance": ready,
            "ready_for_design": ready,
            "design_ready_to_approve": False,
            "spec_enhanced": False,
            "spec_approved": True,
            "interview_complete": True,
            "spec_compiled": True,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "rewind_notice": "",
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
            },
            pending,
        )

    # A later user turn is never "start discovery". Fresh interview scripts only
    # on the first Phase 0 turn (no prior assistant, typically empty pending).
    if pending and _conversation_already_started(state):
        if is_informational_query(pending):
            return answer_before_approve(
                state,
                pending,
                node="phase0",
                base={
                    "phase": "phase0",
                    "design_track": "unset",
                    "design_step": 0,
                    "ready_to_advance": False,
                    "interview_questions": interview_questions,
                    "interview_answers": interview_answers,
                    "current_question_index": current_question_index,
                    "interview_complete": False,
                    "spec_compiled": False,
                },
            )
        return _conduct_phase0_turn(
            state,
            pending=pending,
            business_spec=business_spec,
            history_tail=history_tail,
            interview_questions=list(interview_questions or []),
            current_question_index=int(current_question_index or 0),
            interview_answers=dict(interview_answers or {}),
        )
    
    # First discovery turn: generate interview questions
    system_type_result = invoke_json(
        system=(
            "You are the Architect agent's Phase 0 classifier (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            "Task: Extract the core system type from the vague request.\n"
            "Identify what kind of system this is (e.g., 'video sharing platform', 'e-commerce', "
            "'social media', 'messaging app', 'file storage', etc.).\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "system_type": string,\n'
            '  "search_query": string\n'
            "}\n"
            "search_query: a web search query to find similar systems and their specifications.\n"
        ),
        user=(
            f"Current living specification:\n\n{business_spec}\n\n"
            f"{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none — initial classification)'}\n"
        ),
    )
    
    system_type = system_type_result.get("system_type", "system")
    search_query = system_type_result.get("search_query", f"{system_type} architecture requirements specification")
    
    # Perform web search to find similar systems
    search_results = perform_web_search(search_query, num_results=5)
    
    # Generate interview questions from search results
    questions_result = invoke_json(
        system=(
            "You are the Architect agent's Phase 0 interview question generator (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            "Task: Generate a list of interview questions to gather comprehensive requirements.\n"
            "Use the search results to understand what information is typically needed for similar systems.\n"
            "Create 5-10 specific questions that will help build a complete specification.\n"
            "Each question must name a concrete choice (who, which data, which constraint, "
            "or which alternative) and be answerable in one sentence. Avoid yes/no questions.\n"
            "NEVER ask them to 'tell me more about your system' or 'provide more details' "
            "without naming the missing fact.\n"
            "Honor DISCUSSION MEMORY: do not ask about issues already settled.\n"
            f"{TRACK_CLASSIFICATION_RULES}\n"
            "If the user wants a local/self-contained/stand-alone app, ask about on-device "
            "storage, single-user vs local multi-user, and offline — NOT CDN, multi-region, "
            "or microservices.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "questions": [\n'
            '    {"id": string, "text": string, "category": string}\n'
            '  ],\n'
            '  "assistant_message": string\n'
            "}\n"
            "id should be a short identifier like 'q1', 'q2', etc.\n"
            "category can be: 'business', 'technical', 'users', 'constraints', 'success_metrics'\n"
            "assistant_message should ask the first specific question. If Latest user "
            "message is not (none), address that message first — do not introduce the "
            "interview as if this were a first turn with no user reply."
        ),
        user=(
            f"System type: {system_type}\n\n"
            f"Web search results:\n{search_results}\n\n"
            f"Current living specification:\n\n{business_spec}\n\n"
            f"{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none — first discovery turn)'}\n"
        ),
    )
    
    questions = questions_result.get("questions", [])
    if not isinstance(questions, list):
        questions = []
    questions = [q for q in questions if isinstance(q, dict) and str(q.get("text") or "").strip()]
    assistant_message = str(questions_result.get("assistant_message") or "").strip()
    asked = extract_question_titles(
        [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
    )
    
    # If we have questions, start the interview
    if questions:
        first_question = str(questions[0].get("text") or "").strip()
        body = assistant_message or first_question
        spec = living_spec_scaffold(business_spec)
        if pending:
            follow, _ready = specific_followup_message(spec, pending, asked)
            if follow and follow not in body:
                body = follow if not assistant_message else f"{assistant_message}\n\n{follow}"
            spec = _fold_decision_into_spec(spec, pending)
        return _remember(
            state,
            {
            "phase": "phase0",
            "design_track": "unset",
            "design_step": 0,
            "business_spec": spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": False,
            "ready_for_design": False,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "interview_questions": questions,
            "interview_answers": {},
            "current_question_index": 0,
            "interview_complete": False,
            "spec_compiled": False,
            "pending_user_feedback": "",
            "pending_assistant_message": body,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": body, "node": "phase0"}],
            },
            pending,
        )
    
    follow, ready = specific_followup_message(business_spec, pending, asked)
    return _remember(
        state,
        {
        "phase": "phase0",
        "design_track": "unset",
        "design_step": 0,
        "business_spec": business_spec,
        "tradeoff_ledger": state.get("tradeoff_ledger") or "",
        "ready_to_advance": ready,
        "ready_for_design": ready,
        "design_ready_to_approve": False,
        "spec_enhanced": False,
        "pending_user_feedback": "",
        "pending_assistant_message": follow,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "messages": [{"role": "assistant", "content": follow, "node": "phase0"}],
        },
        pending,
    )


def phase0_wait_node(state: DesignGraphState) -> dict[str, Any]:
    track = state.get("design_track") or "unset"
    step = int(state.get("design_step") or 0)
    ready = bool(state.get("ready_to_advance"))
    label = approve_label("phase0", track, step)
    assistant = with_next_prompt(
        state.get("pending_assistant_message") or "",
        approve_label=label,
        can_approve=ready,
    )

    resume = interrupt(
        {
            "phase": "phase0",
            "design_track": track,
            "design_step": step,
            "assistant_message": assistant,
            "business_spec": state.get("business_spec") or "",
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": ready,
            "can_approve": ready,
            "approve_kind": "advance",
            "approve_label": label,
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    keynotes, kind, clar = gate_user_chat(
        state,
        user_text,
        action=action,
        node="phase0",
        stay={
            "phase": "phase0",
            "design_track": track,
            "design_step": step,
            "ready_to_advance": ready,
        },
    )
    if clar:
        return clar
    state["discussion_digest"] = keynotes
    advance_now = is_advance_request(user_text)
    action = promote_chat_to_approve(action, user_text, can_approve=ready)
    if kind == "approve" and ready and action == "chat":
        action = "approve"
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "phase0"})

    if action == "approve" and track in {"lld", "hld"}:
        enter_msg = (
            f"Wrapping up discovery and moving on. Starting **{track.upper()}** track."
            if advance_now
            else f"Starting **{track.upper()}** track."
        )
        msgs.append({"role": "assistant", "content": enter_msg, "node": "phase0"})
        return {
            "phase": track,  # type: ignore[typeddict-item]
            "design_track": track,  # type: ignore[typeddict-item]
            "design_step": 1,
            "ready_to_advance": False,
            "pending_user_feedback": "",
            "pending_assistant_message": enter_msg,
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "phase0"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if user_text:
        rewound = rewind_or_block_skip(
            state,
            user_text,
            node="phase0",
            current_phase="phase0",
            current_track=track if track in {"unset", "lld", "hld"} else "unset",
            current_step=0,
            msgs=msgs,
        )
        if rewound is not None:
            return rewound

    if user_text and is_informational_query(user_text):
        return answer_before_approve(
            state,
            user_text,
            node="phase0",
            base={
                "phase": "phase0",
                "design_track": track,
                "design_step": 0,
                "ready_to_advance": ready,
            },
        )

    return {
        "phase": "phase0",
        "design_track": track,  # type: ignore[typeddict-item]
        "design_step": 0,
        "ready_to_advance": False,
        "pending_user_feedback": user_text,
        "stay_on_interrupt": False,
        "discussion_digest": keynotes,
        "messages": msgs,
    }
