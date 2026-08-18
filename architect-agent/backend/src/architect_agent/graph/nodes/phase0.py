from __future__ import annotations

import json
from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_history_tail,
    maybe_compact_business_spec,
)
from architect_agent.graph.nodes.common import answer_before_approve, approve_label, invoke_json
from architect_agent.graph.state import DesignGraphState
from architect_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    is_informational_query,
    promote_chat_to_approve,
    with_resolution_close,
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


def phase0_classify_node(state: DesignGraphState) -> dict[str, Any]:
    """Phase 0: interactive question-based interview to gather comprehensive requirements."""
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
    history_tail = format_history_tail(prior)
    track = state.get("design_track") or "unset"
    
    # Check if interview is complete and spec is compiled
    interview_complete = state.get("interview_complete", False)
    spec_compiled = state.get("spec_compiled", False)
    
    # If we have a compiled spec and user wants to proceed with classification
    if spec_compiled and interview_complete:
        # Check if user wants to make final edits
        if pending and pending.lower() in ["yes", "y", "add", "edit", "update", "modify"]:
            # Process final edits
            spec_update_result = invoke_json(
                system=(
                    "You are the Architect agent's Phase 0 spec refiner (Principal Architect).\n"
                    f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                    f"{JSON_OUTPUT_DIGEST}\n\n"
                    "Task: Update the business specification based on user's final edits.\n"
                    "Integrate the user's requested changes into the spec.\n"
                    "After updating, classify as LLD vs HLD and set ready_to_advance=true.\n"
                    "Respond ONLY with JSON:\n"
                    "{\n"
                    '  "updated_business_spec": string,\n'
                    '  "design_track": "unset" | "lld" | "hld",\n'
                    '  "ready_to_advance": boolean,\n'
                    '  "assistant_message": string\n'
                    "}\n"
                ),
                user=(
                    f"Current living specification:\n\n{business_spec}\n\n"
                    f"User's requested changes:\n{pending}\n"
                ),
            )
            
            new_track = str(spec_update_result.get("design_track") or "unset").lower()
            if new_track not in {"unset", "lld", "hld"}:
                new_track = "unset"
            ready = bool(spec_update_result.get("ready_to_advance")) and new_track in {"lld", "hld"}
            spec = spec_update_result.get("updated_business_spec") or business_spec
            assistant = str(spec_update_result.get("assistant_message") or "")
            
            return {
                "phase": "phase0",
                "design_track": new_track,  # type: ignore[typeddict-item]
                "design_step": 0,
                "business_spec": spec,
                "tradeoff_ledger": state.get("tradeoff_ledger") or "",
                "ready_to_advance": ready,
                "ready_for_design": ready,
                "design_ready_to_approve": False,
                "spec_enhanced": True,
                "interview_complete": True,
                "spec_compiled": True,
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "publish_requested": False,
                "stay_on_interrupt": False,
                "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
            }
        else:
            # User is satisfied with the spec, proceed to classification
            result = invoke_json(
                system=(
                    "You are the Architect agent's Phase 0 classifier (Principal Architect).\n"
                    f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                    f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
                    f"{JSON_OUTPUT_DIGEST}\n\n"
                    "Task: classify LLD vs HLD from the compiled spec.\n"
                    "HLD if the idea is a product/platform spanning network, storage, CDN, "
                    "multi-user scale, or 'like YouTube/Uber/SaaS'. LLD if it is a library, CLI, "
                    "or single OS process.\n"
                    "Set design_track to lld or hld and ready_to_advance=true.\n"
                    "assistant_message should say the classification and invite Approve to start the track.\n"
                    "Respond ONLY with JSON:\n"
                    "{\n"
                    '  "design_track": "unset" | "lld" | "hld",\n'
                    '  "ready_to_advance": boolean,\n'
                    '  "updated_business_spec": string,\n'
                    '  "tradeoff_ledger": string,\n'
                    '  "assistant_message": string\n'
                    "}\n"
                    "updated_business_spec: keep the spec as-is.\n"
                    "tradeoff_ledger: one line noting the classification assumption.\n"
                ),
                user=(
                    f"Current living specification:\n\n{business_spec}\n\n"
                    f"User's final response: {pending or 'No changes requested'}\n"
                ),
            )
            
            new_track = str(result.get("design_track") or "unset").lower()
            if new_track not in {"unset", "lld", "hld"}:
                new_track = "unset"
            ready = bool(result.get("ready_to_advance")) and new_track in {"lld", "hld"}
            spec = result.get("updated_business_spec") or business_spec
            ledger = result.get("tradeoff_ledger") or state.get("tradeoff_ledger") or ""
            assistant = (
                result.get("assistant_message")
                or f"Scope classified as **{new_track.upper()}**. Click approve to begin the track."
            )
            
            return {
                "phase": "phase0",
                "design_track": new_track,  # type: ignore[typeddict-item]
                "design_step": 0,
                "business_spec": spec,
                "tradeoff_ledger": ledger,
                "ready_to_advance": ready,
                "ready_for_design": ready,
                "design_ready_to_approve": False,
                "spec_enhanced": True,
                "interview_complete": True,
                "spec_compiled": True,
                "pending_user_feedback": "",
                "pending_assistant_message": assistant,
                "publish_requested": False,
                "stay_on_interrupt": False,
                "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
            }
    
    # If interview is complete but spec not yet compiled, compile the spec
    if interview_complete and not spec_compiled:
        interview_answers = state.get("interview_answers", {})
        interview_questions = state.get("interview_questions", [])
        
        # Compile the spec from answers
        spec_compilation_result = invoke_json(
            system=(
                "You are the Architect agent's Phase 0 spec compiler (Principal Architect).\n"
                f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                f"{JSON_OUTPUT_DIGEST}\n\n"
                "Task: Compile a comprehensive business specification from interview answers.\n"
                "Use the questions and answers to create a well-structured spec.\n"
                "Include sections like: Executive Summary, Core Business Requirements, Non-Functional Requirements, "
                "Technical Constraints, Success Metrics, etc.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "compiled_spec": string,\n'
                '  "assistant_message": string\n'
                "}\n"
                "assistant_message should present the compiled spec and ask if the user wants to add or update anything."
            ),
            user=(
                f"Original request:\n\n{business_spec}\n\n"
                f"Interview questions and answers:\n"
                f"{json.dumps({'questions': interview_questions, 'answers': interview_answers}, indent=2)}\n"
            ),
        )
        
        compiled_spec = spec_compilation_result.get("compiled_spec") or business_spec
        assistant = spec_compilation_result.get("assistant_message") or "Here is your compiled specification. Would you like to add or update anything?"
        
        return {
            "phase": "phase0",
            "design_track": "unset",
            "design_step": 0,
            "business_spec": compiled_spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": False,
            "ready_for_design": False,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "interview_complete": True,
            "spec_compiled": True,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
        }
    
    # If interview is in progress, ask the next question
    interview_questions = state.get("interview_questions", [])
    current_question_index = state.get("current_question_index", 0)
    interview_answers = state.get("interview_answers", {})
    
    # If we have interview questions, check if we need to ask the next one
    if interview_questions and current_question_index < len(interview_questions):
        # User provided an answer to the current question
        if pending:
            current_question = interview_questions[current_question_index]
            question_id = current_question.get("id", f"q{current_question_index}")
            interview_answers[question_id] = pending
            
            # Move to next question
            next_index = current_question_index + 1
            
            if next_index >= len(interview_questions):
                # All questions answered, mark interview as complete
                return {
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
                    "current_question_index": next_index,
                    "interview_complete": True,
                    "spec_compiled": False,
                    "pending_user_feedback": "",
                    "pending_assistant_message": "Thank you for answering all the questions. I'll now compile your specification.",
                    "publish_requested": False,
                    "stay_on_interrupt": False,
                    "messages": [{"role": "assistant", "content": "Thank you for answering all the questions. I'll now compile your specification.", "node": "phase0"}],
                }
            else:
                # Ask next question
                next_question = interview_questions[next_index]
                return {
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
                    "current_question_index": next_index,
                    "interview_complete": False,
                    "spec_compiled": False,
                    "pending_user_feedback": "",
                    "pending_assistant_message": next_question.get("text", ""),
                    "publish_requested": False,
                    "stay_on_interrupt": False,
                    "messages": [{"role": "assistant", "content": next_question.get("text", ""), "node": "phase0"}],
                }
        else:
            # Ask the current question
            current_question = interview_questions[current_question_index]
            return {
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
            }
    
    # If spec is already comprehensive, use original classification logic
    spec_comprehensive = _spec_looks_structured(business_spec)
    if spec_comprehensive:
        result = invoke_json(
            system=(
                "You are the Architect agent's Phase 0 classifier (Principal Architect).\n"
                f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
                f"{JSON_OUTPUT_DIGEST}\n\n"
                "Task: classify LLD vs HLD from the spec on THIS turn if possible.\n"
                "HLD if the idea is a product/platform spanning network, storage, CDN, "
                "multi-user scale, or 'like YouTube/Uber/SaaS'. LLD if it is a library, CLI, "
                "or single OS process.\n"
                "Do NOT ask for DAU/QPS/bitrate here — that is HLD Step 1.\n"
                "If classification is already clear, set design_track to lld or hld and "
                "ready_to_advance=true. assistant_message should say the classification and "
                "invite Approve to start the track.\n"
                "Ask ONE clarifying question ONLY if LLD vs HLD is truly ambiguous; still "
                "propose a (Recommended) track.\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "design_track": "unset" | "lld" | "hld",\n'
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
                f"Recent Phase 0 turns:\n{history_tail}\n\n"
                f"Latest user message:\n{pending or '(none — initial classification)'}\n"
            ),
        )

        new_track = str(result.get("design_track") or "unset").lower()
        if new_track not in {"unset", "lld", "hld"}:
            new_track = "unset"
        ready = bool(result.get("ready_to_advance")) and new_track in {"lld", "hld"}
        spec = result.get("updated_business_spec") or business_spec
        ledger = result.get("tradeoff_ledger") or state.get("tradeoff_ledger") or ""
        assistant = (
            result.get("assistant_message")
            or (
                f"Scope classified as **{new_track.upper()}**. Click approve to begin the track."
                if ready
                else "I need a bit more detail to classify LLD vs HLD."
            )
        )
        if pending:
            assistant = with_resolution_close(
                str(assistant),
                changed=new_track != str(state.get("design_track") or "unset")
                or spec != business_spec,
            )

        return {
            "phase": "phase0",
            "design_track": new_track,  # type: ignore[typeddict-item]
            "design_step": 0,
            "business_spec": spec,
            "tradeoff_ledger": ledger,
            "ready_to_advance": ready,
            "ready_for_design": ready,
            "design_ready_to_approve": False,
            "spec_enhanced": False,
            "pending_user_feedback": "",
            "pending_assistant_message": assistant,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
        }
    
    # If spec is not comprehensive, generate interview questions
    # Extract key system type from the vague request
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
            "Each question should be clear and focused on one aspect of the system.\n"
            "Avoid yes/no questions; ask for specific details.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "questions": [\n'
            '    {"id": string, "text": string, "category": string}\n'
            '  ],\n'
            '  "assistant_message": string\n'
            "}\n"
            "id should be a short identifier like 'q1', 'q2', etc.\n"
            "category can be: 'business', 'technical', 'users', 'constraints', 'success_metrics'\n"
            "assistant_message should introduce the interview process and ask the first question."
        ),
        user=(
            f"System type: {system_type}\n\n"
            f"Web search results:\n{search_results}\n\n"
            f"Current living specification:\n\n{business_spec}\n\n"
        ),
    )
    
    questions = questions_result.get("questions", [])
    assistant_message = questions_result.get("assistant_message", "")
    
    # If we have questions, start the interview
    if questions:
        first_question = questions[0].get("text", "") if questions else ""
        return {
            "phase": "phase0",
            "design_track": "unset",
            "design_step": 0,
            "business_spec": business_spec,
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
            "pending_assistant_message": assistant_message or first_question,
            "publish_requested": False,
            "stay_on_interrupt": False,
            "messages": [{"role": "assistant", "content": assistant_message or first_question, "node": "phase0"}],
        }
    
    # Fallback to original behavior if no questions generated
    return {
        "phase": "phase0",
        "design_track": "unset",
        "design_step": 0,
        "business_spec": business_spec,
        "tradeoff_ledger": state.get("tradeoff_ledger") or "",
        "ready_to_advance": False,
        "ready_for_design": False,
        "design_ready_to_approve": False,
        "spec_enhanced": False,
        "pending_user_feedback": "",
        "pending_assistant_message": "I need more information to proceed. Please provide more details about your system.",
        "publish_requested": False,
        "stay_on_interrupt": False,
        "messages": [{"role": "assistant", "content": "I need more information to proceed. Please provide more details about your system.", "node": "phase0"}],
    }


def phase0_wait_node(state: DesignGraphState) -> dict[str, Any]:
    track = state.get("design_track") or "unset"
    step = int(state.get("design_step") or 0)
    ready = bool(state.get("ready_to_advance"))
    assistant = state.get("pending_assistant_message") or ""

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
            "approve_label": approve_label("phase0", track, step),
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    action = promote_chat_to_approve(action, user_text, can_approve=ready)
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "phase0"})

    if action == "approve" and ready and track in {"lld", "hld"}:
        enter_msg = f"Starting **{track.upper()}** track."
        msgs.append({"role": "assistant", "content": enter_msg, "node": "phase0"})
        return {
            "phase": track,  # type: ignore[typeddict-item]
            "design_track": track,  # type: ignore[typeddict-item]
            "design_step": 1,
            "ready_to_advance": False,
            "pending_user_feedback": "",
            "pending_assistant_message": enter_msg,
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "phase0"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "messages": msgs,
        }

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
        "messages": msgs,
    }
