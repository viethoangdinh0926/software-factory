from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_history_tail,
    maybe_compact_business_spec,
)
from architect_agent.graph.nodes.common import approve_label, invoke_json
from architect_agent.graph.state import DesignGraphState
from architect_agent.web_search import perform_web_search


def phase0_classify_node(state: DesignGraphState) -> dict[str, Any]:
    """Phase 0: classify LLD vs HLD and gather comprehensive requirements."""
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "phase0"]
    history_tail = format_history_tail(prior)
    track = state.get("design_track") or "unset"
    spec_enhanced = state.get("spec_enhanced", False)

    if track in {"lld", "hld"} and state.get("ready_to_advance"):
        return {
            "phase": "phase0",
            "design_track": track,
            "design_step": 0,
            "ready_to_advance": True,
            "pending_user_feedback": "",
            "pending_assistant_message": state.get("pending_assistant_message") or "",
        }

    # Check if spec is comprehensive enough (has at least 500 characters and multiple sections)
    spec_comprehensive = len(business_spec) > 500 and "##" in business_spec
    
    # If spec is not comprehensive and hasn't been enhanced yet, perform web search
    if not spec_comprehensive and not spec_enhanced:
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
        
        # Generate comprehensive spec template from search results
        spec_template_result = invoke_json(
            system=(
                "You are the Architect agent's Phase 0 spec template generator (Principal Architect).\n"
                f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                f"{JSON_OUTPUT_DIGEST}\n\n"
                "Task: Generate a comprehensive business specification template based on web search results.\n"
                "Use the search results to understand common features and requirements for similar systems.\n"
                "Create a detailed spec template with placeholders for the user to confirm/specify values.\n"
                "Include sections like: Executive Summary, Core Business Requirements, Non-Functional Requirements, "
                "Technical Constraints, Success Metrics, etc.\n"
                "For each feature/property, use a clear placeholder like [CONFIRM: value] or [SPECIFY: value].\n"
                "Respond ONLY with JSON:\n"
                "{\n"
                '  "updated_business_spec": string,\n'
                '  "design_track": "unset" | "lld" | "hld",\n'
                '  "ready_to_advance": boolean,\n'
                '  "assistant_message": string\n'
                "}\n"
                "ready_to_advance should be false until user confirms the spec template.\n"
                "assistant_message should explain the spec template and ask user to confirm/specify values.\n"
            ),
            user=(
                f"System type: {system_type}\n\n"
                f"Web search results:\n{search_results}\n\n"
                f"Current living specification:\n\n{business_spec}\n\n"
            ),
        )
        
        new_track = str(spec_template_result.get("design_track") or "unset").lower()
        if new_track not in {"unset", "lld", "hld"}:
            new_track = "unset"
        
        return {
            "phase": "phase0",
            "design_track": new_track,  # type: ignore[typeddict-item]
            "design_step": 0,
            "business_spec": spec_template_result.get("updated_business_spec") or business_spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": False,
            "ready_for_design": False,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "pending_user_feedback": "",
            "pending_assistant_message": spec_template_result.get("assistant_message") or "",
            "publish_requested": False,
            "messages": [{"role": "assistant", "content": spec_template_result.get("assistant_message") or "", "node": "phase0"}],
        }

    # If spec has been enhanced, check if user has provided feedback
    if spec_enhanced:
        # Process user feedback and update spec
        spec_update_result = invoke_json(
            system=(
                "You are the Architect agent's Phase 0 spec refiner (Principal Architect).\n"
                f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
                f"{JSON_OUTPUT_DIGEST}\n\n"
                "Task: Update the business specification based on user feedback.\n"
                "Replace placeholders [CONFIRM: value] or [SPECIFY: value] with user-provided values.\n"
                "If user confirms a placeholder, remove the placeholder and use the confirmed value.\n"
                "If user provides new requirements, integrate them into the spec.\n"
                "After updating, check if the spec is comprehensive enough (no more placeholders).\n"
                "If spec is comprehensive, classify as LLD vs HLD and set ready_to_advance=true.\n"
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
                f"Recent Phase 0 turns:\n{history_tail}\n\n"
                f"Latest user message:\n{pending or '(none — spec refinement)'}\n"
            ),
        )
        
        new_track = str(spec_update_result.get("design_track") or "unset").lower()
        if new_track not in {"unset", "lld", "hld"}:
            new_track = "unset"
        ready = bool(spec_update_result.get("ready_to_advance")) and new_track in {"lld", "hld"}
        
        return {
            "phase": "phase0",
            "design_track": new_track,  # type: ignore[typeddict-item]
            "design_step": 0,
            "business_spec": spec_update_result.get("updated_business_spec") or business_spec,
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "ready_to_advance": ready,
            "ready_for_design": ready,
            "design_ready_to_approve": False,
            "spec_enhanced": True,
            "pending_user_feedback": "",
            "pending_assistant_message": spec_update_result.get("assistant_message") or "",
            "publish_requested": False,
            "messages": [{"role": "assistant", "content": spec_update_result.get("assistant_message") or "", "node": "phase0"}],
        }

    # Original classification logic for when spec is already comprehensive
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
            "Escape newlines in strings as \\n."
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
        "messages": [{"role": "assistant", "content": assistant, "node": "phase0"}],
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
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "phase0"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "messages": msgs,
        }

    return {
        "phase": "phase0",
        "design_track": track,  # type: ignore[typeddict-item]
        "design_step": 0,
        "ready_to_advance": False,
        "pending_user_feedback": user_text,
        "messages": msgs,
    }
