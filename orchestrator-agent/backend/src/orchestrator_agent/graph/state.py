from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict


def _append_messages(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return (left or []) + (right or [])


class OrchestratorGraphState(TypedDict):
    design_session_id: str
    package_markdown: str
    pending_package: str
    design_version: int
    architect_track: str
    topology: str
    topology_certain: bool
    design_diagram: str
    ingest_kind: str
    has_ingested: bool
    previous_track: str
    previous_topology: str
    previous_services: list[dict[str, Any]]
    previous_app_status: str
    services: list[dict[str, Any]]
    active_service_id: str
    tech_stack: str
    feature_spec: str
    plan_spec: str
    api_type: str
    api_design: str
    app_status: str
    search_notes: str
    messages: Annotated[list[dict[str, Any]], _append_messages]
    phase: str
    wait_kind: str
    route: str
    pending_user_feedback: str
    pending_assistant_message: str
    pending_engineer_actions: list[dict[str, Any]]
    can_approve: bool
    approve_kind: str
    approve_label: str
    finalized: bool
    last_interrupt: NotRequired[dict[str, Any]]
