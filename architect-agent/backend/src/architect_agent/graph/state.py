from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from typing_extensions import NotRequired


NodeName = Literal[
    "phase0",
    "lld",
    "hld",
    "market_research",
    "spec_interview",
    "system_design",
]


class ChatTurn(TypedDict):
    role: Literal["assistant", "user", "system"]
    content: str
    node: NodeName


def _merge_spec(left: str | None, right: str | None) -> str:
    if right is None:
        return left or ""
    return right


def _append_messages(
    left: list[ChatTurn] | None,
    right: list[ChatTurn] | None,
) -> list[ChatTurn]:
    return (left or []) + (right or [])


def _replace_str(left: str | None, right: str | None) -> str:
    if right is None:
        return left or ""
    return right


def _replace_bool(left: bool | None, right: bool | None) -> bool:
    if right is None:
        return bool(left)
    return bool(right)


def _replace_int(left: int | None, right: int | None) -> int:
    if right is None:
        return int(left or 0)
    return int(right)


DesignTrack = Literal["unset", "lld", "hld"]
PhaseName = Literal["phase0", "lld", "hld", "market_research", "done"]


class DesignGraphState(TypedDict):
    session_id: str
    business_spec: Annotated[str, _merge_spec]
    messages: Annotated[list[ChatTurn], _append_messages]
    phase: PhaseName
    design_track: DesignTrack
    design_step: Annotated[int, _replace_int]
    ready_for_design: bool
    ready_to_advance: bool
    design_ready_to_approve: bool
    spec_approved: bool
    design_diagram: Annotated[str, _replace_str]
    design_justification: Annotated[str, _replace_str]
    design_approved: bool
    pending_user_feedback: Annotated[str, _replace_str]
    publish_requested: bool
    pending_assistant_message: NotRequired[str]
    last_interrupt: NotRequired[dict[str, Any]]
    market_evaluation_report: Annotated[str, _replace_str]
    market_evaluation_grade: Annotated[str, _replace_str]
    market_evaluation_done: Annotated[bool, _replace_bool]
    tradeoff_ledger: Annotated[str, _replace_str]
    scale_estimates: Annotated[str, _replace_str]
    api_contracts: Annotated[str, _replace_str]
    communication_schemes: Annotated[str, _replace_str]
    fmea_notes: Annotated[str, _replace_str]
    spec_enhanced: NotRequired[bool]
    rewind_notice: NotRequired[str]
    carry_change: Annotated[str, _replace_str]
    rewalk_until_step: Annotated[int, _replace_int]
    discussion_digest: Annotated[str, _replace_str]
    # After market continue: resume_track + resume_step for handoff loop.
    resume_after_market: Annotated[bool, _replace_bool]
    # Chat answered a question: loop back to the same wait node without regenerating.
    stay_on_interrupt: Annotated[bool, _replace_bool]
    # Phase 0 interview state
    interview_questions: NotRequired[list[dict[str, Any]]]  # List of questions to ask
    interview_answers: NotRequired[dict[str, str]]  # Map of question_id to answer
    current_question_index: NotRequired[int]  # Index of current question being asked
    interview_complete: NotRequired[bool]  # Whether all questions have been answered
    spec_compiled: NotRequired[bool]  # Whether the spec has been compiled from answers
