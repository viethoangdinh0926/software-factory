from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from typing_extensions import NotRequired


class ChatTurn(TypedDict):
    role: Literal["assistant", "user", "system"]
    content: str
    node: Literal["spec_interview", "system_design"]


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


class DesignGraphState(TypedDict):
    session_id: str
    business_spec: Annotated[str, _merge_spec]
    messages: Annotated[list[ChatTurn], _append_messages]
    phase: Literal["spec_interview", "system_design", "done"]
    ready_for_design: bool
    spec_approved: bool
    design_diagram: Annotated[str, _replace_str]
    design_justification: Annotated[str, _replace_str]
    design_approved: bool
    pending_user_feedback: Annotated[str, _replace_str]
    publish_requested: bool
    pending_assistant_message: NotRequired[str]
    last_interrupt: NotRequired[dict[str, Any]]
