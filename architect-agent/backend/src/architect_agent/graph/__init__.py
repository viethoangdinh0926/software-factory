from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from architect_agent.config import BACKEND_ROOT
from architect_agent.graph.nodes.market_research import market_research_node, market_wait_node
from architect_agent.graph.nodes.spec_interview import spec_interview_node, spec_wait_node
from architect_agent.graph.nodes.system_design import design_wait_node, system_design_node
from architect_agent.graph.state import DesignGraphState

_checkpointer: SqliteSaver | None = None
_sqlite_conn: sqlite3.Connection | None = None
_compiled: CompiledStateGraph | None = None


def _checkpoint_path() -> Path:
    path = BACKEND_ROOT / "data" / "checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_checkpointer() -> SqliteSaver:
    """Process-wide SQLite checkpointer so design sessions survive process restarts."""
    global _checkpointer, _sqlite_conn
    if _checkpointer is None:
        _sqlite_conn = sqlite3.connect(str(_checkpoint_path()), check_same_thread=False)
        _checkpointer = SqliteSaver(_sqlite_conn)
        _checkpointer.setup()
    return _checkpointer


def _after_spec_wait(
    state: DesignGraphState,
) -> Literal["spec_interview", "market_research", "spec_wait"]:
    if state.get("spec_approved") and not state.get("market_evaluation_done"):
        return "market_research"
    if state.get("spec_approved"):
        # Should not normally happen; keep interview loop safe.
        return "market_research"
    if state.get("pending_user_feedback"):
        return "spec_interview"
    return "spec_wait"


def _after_market_wait(state: DesignGraphState) -> Literal["system_design"]:
    return "system_design"


def _after_design_wait(
    state: DesignGraphState,
) -> Literal["system_design", "design_wait"]:
    if state.get("publish_requested") or not (state.get("pending_user_feedback") or "").strip():
        return "design_wait"
    return "system_design"


def build_graph() -> CompiledStateGraph:
    global _compiled
    if _compiled is not None:
        return _compiled

    graph = StateGraph(DesignGraphState)
    graph.add_node("spec_interview", spec_interview_node)
    graph.add_node("spec_wait", spec_wait_node)
    graph.add_node("market_research", market_research_node)
    graph.add_node("market_wait", market_wait_node)
    graph.add_node("system_design", system_design_node)
    graph.add_node("design_wait", design_wait_node)

    graph.add_edge(START, "spec_interview")
    graph.add_edge("spec_interview", "spec_wait")
    graph.add_conditional_edges(
        "spec_wait",
        _after_spec_wait,
        {
            "spec_interview": "spec_interview",
            "market_research": "market_research",
            "spec_wait": "spec_wait",
        },
    )
    graph.add_edge("market_research", "market_wait")
    graph.add_conditional_edges(
        "market_wait",
        _after_market_wait,
        {"system_design": "system_design"},
    )
    graph.add_edge("system_design", "design_wait")
    graph.add_conditional_edges(
        "design_wait",
        _after_design_wait,
        {
            "system_design": "system_design",
            "design_wait": "design_wait",
        },
    )

    _compiled = graph.compile(checkpointer=get_checkpointer())
    return _compiled


def reset_graph() -> None:
    """Test helper to clear the singleton compiled graph (keeps SQLite DB file)."""
    global _compiled
    _compiled = None


def initial_state(session_id: str, markdown: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "business_spec": markdown,
        "messages": [],
        "phase": "spec_interview",
        "ready_for_design": False,
        "spec_approved": False,
        "design_diagram": "",
        "design_justification": "",
        "design_approved": False,
        "pending_user_feedback": "",
        "publish_requested": False,
        "pending_assistant_message": "",
        "market_evaluation_report": "",
        "market_evaluation_grade": "",
        "market_evaluation_done": False,
    }
