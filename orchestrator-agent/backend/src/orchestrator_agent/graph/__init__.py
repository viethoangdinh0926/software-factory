from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator_agent.config import BACKEND_ROOT
from orchestrator_agent.graph.nodes.api import api_design_propose_node, api_type_research_node
from orchestrator_agent.graph.nodes.classify import classify_node
from orchestrator_agent.graph.nodes.ingest import handle_update_node, ingest_node
from orchestrator_agent.graph.nodes.services import extract_services_node, prime_all_services_node
from orchestrator_agent.graph.nodes.stack import emit_plan_node, stack_research_node
from orchestrator_agent.graph.nodes.wait import wait_node
from orchestrator_agent.graph.state import OrchestratorGraphState

_checkpointer: SqliteSaver | None = None
_sqlite_conn: sqlite3.Connection | None = None
_compiled: CompiledStateGraph | None = None


def _checkpoint_path() -> Path:
    path = BACKEND_ROOT / "data" / "checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_checkpointer() -> SqliteSaver:
    global _checkpointer, _sqlite_conn
    if _checkpointer is None:
        _sqlite_conn = sqlite3.connect(str(_checkpoint_path()), check_same_thread=False)
        _checkpointer = SqliteSaver(_sqlite_conn)
        _checkpointer.setup()
    return _checkpointer


def _route(state: OrchestratorGraphState) -> str:
    if state.get("phase") == "done" or state.get("finalized"):
        return "end"
    return str(state.get("route") or "wait")


_WAIT_MAP: dict[str, Any] = {
    "ingest": "ingest",
    "classify": "classify",
    "handle_update": "handle_update",
    "extract_services": "extract_services",
    "prime_all": "prime_all",
    "api_type_research": "api_type_research",
    "api_design_propose": "api_design_propose",
    "stack_research": "stack_research",
    "emit_plan": "emit_plan",
    "wait": "wait",
    "distributed": "wait",
    "end": END,
}


def build_graph() -> CompiledStateGraph:
    global _compiled
    if _compiled is not None:
        return _compiled

    graph = StateGraph(OrchestratorGraphState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("classify", classify_node)
    graph.add_node("handle_update", handle_update_node)
    graph.add_node("extract_services", extract_services_node)
    graph.add_node("prime_all", prime_all_services_node)
    graph.add_node("api_type_research", api_type_research_node)
    graph.add_node("api_design_propose", api_design_propose_node)
    graph.add_node("stack_research", stack_research_node)
    graph.add_node("emit_plan", emit_plan_node)
    graph.add_node("wait", wait_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "classify")
    graph.add_conditional_edges(
        "classify",
        _route,
        {"handle_update": "handle_update", "wait": "wait", "end": END},
    )
    graph.add_conditional_edges(
        "handle_update",
        _route,
        {
            "stack_research": "stack_research",
            "extract_services": "extract_services",
            "wait": "wait",
            "end": END,
        },
    )
    graph.add_edge("extract_services", "prime_all")
    graph.add_edge("prime_all", "wait")
    graph.add_edge("api_type_research", "wait")
    graph.add_edge("api_design_propose", "wait")
    graph.add_edge("stack_research", "wait")
    graph.add_conditional_edges(
        "emit_plan",
        _route,
        {"wait": "wait", "end": END},
    )
    graph.add_conditional_edges("wait", _route, {**_WAIT_MAP, "__end__": END})

    _compiled = graph.compile(checkpointer=get_checkpointer())
    return _compiled


def reset_graph() -> None:
    global _compiled
    _compiled = None


def initial_state(session_id: str, markdown: str) -> dict[str, Any]:
    return {
        "design_session_id": session_id,
        "package_markdown": markdown,
        "pending_package": markdown,
        "design_version": 0,
        "architect_track": "unset",
        "topology": "unset",
        "topology_certain": False,
        "design_diagram": "",
        "ingest_kind": "first",
        "has_ingested": False,
        "previous_track": "",
        "previous_topology": "unset",
        "previous_services": [],
        "previous_app_status": "",
        "services": [],
        "active_service_id": "",
        "tech_stack": "",
        "plan_spec": "",
        "api_type": "",
        "api_design": "",
        "app_status": "",
        "search_notes": "",
        "messages": [],
        "phase": "ingest",
        "wait_kind": "",
        "route": "ingest",
        "pending_user_feedback": "",
        "pending_assistant_message": "",
        "pending_engineer_actions": [],
        "can_approve": False,
        "approve_kind": "",
        "approve_label": "",
        "finalized": False,
    }
