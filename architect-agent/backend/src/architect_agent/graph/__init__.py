from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from architect_agent.config import BACKEND_ROOT
from architect_agent.graph.nodes.hld import hld_step_node, hld_wait_node
from architect_agent.graph.nodes.lld import lld_step_node, lld_wait_node
from architect_agent.graph.nodes.market_research import market_research_node, market_wait_node
from architect_agent.graph.nodes.phase0 import phase0_classify_node, phase0_wait_node
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


def _after_phase0_wait(
    state: DesignGraphState,
) -> Literal["phase0_classify", "phase0_wait", "lld_step", "hld_step", "__end__"]:
    if state.get("phase") == "done":
        return "__end__"
    track = state.get("design_track") or "unset"
    step = int(state.get("design_step") or 0)
    if track == "lld" and step >= 1:
        return "lld_step"
    if track == "hld" and step >= 1:
        return "hld_step"
    if state.get("stay_on_interrupt"):
        return "phase0_wait"
    return "phase0_classify"


def _after_lld_wait(
    state: DesignGraphState,
) -> Literal["lld_step", "lld_wait", "market_research", "__end__"]:
    if state.get("phase") == "done":
        return "__end__"
    if state.get("phase") == "market_research":
        return "market_research"
    if state.get("stay_on_interrupt"):
        return "lld_wait"
    return "lld_step"


def _after_hld_wait(
    state: DesignGraphState,
) -> Literal["hld_step", "hld_wait", "market_research", "__end__"]:
    if state.get("phase") == "done":
        return "__end__"
    if state.get("phase") == "market_research":
        return "market_research"
    if state.get("stay_on_interrupt"):
        return "hld_wait"
    return "hld_step"


def _after_market_wait(
    state: DesignGraphState,
) -> Literal["lld_step", "hld_step", "market_wait"]:
    if state.get("stay_on_interrupt"):
        return "market_wait"
    track = state.get("design_track") or "hld"
    if track == "lld":
        return "lld_step"
    return "hld_step"


def build_graph() -> CompiledStateGraph:
    global _compiled
    if _compiled is not None:
        return _compiled

    graph = StateGraph(DesignGraphState)
    graph.add_node("phase0_classify", phase0_classify_node)
    graph.add_node("phase0_wait", phase0_wait_node)
    graph.add_node("lld_step", lld_step_node)
    graph.add_node("lld_wait", lld_wait_node)
    graph.add_node("hld_step", hld_step_node)
    graph.add_node("hld_wait", hld_wait_node)
    graph.add_node("market_research", market_research_node)
    graph.add_node("market_wait", market_wait_node)

    graph.add_edge(START, "phase0_classify")
    graph.add_edge("phase0_classify", "phase0_wait")
    graph.add_conditional_edges(
        "phase0_wait",
        _after_phase0_wait,
        {
            "phase0_classify": "phase0_classify",
            "phase0_wait": "phase0_wait",
            "lld_step": "lld_step",
            "hld_step": "hld_step",
            "__end__": END,
        },
    )
    graph.add_edge("lld_step", "lld_wait")
    graph.add_conditional_edges(
        "lld_wait",
        _after_lld_wait,
        {
            "lld_step": "lld_step",
            "lld_wait": "lld_wait",
            "market_research": "market_research",
            "__end__": END,
        },
    )
    graph.add_edge("hld_step", "hld_wait")
    graph.add_conditional_edges(
        "hld_wait",
        _after_hld_wait,
        {
            "hld_step": "hld_step",
            "hld_wait": "hld_wait",
            "market_research": "market_research",
            "__end__": END,
        },
    )
    graph.add_edge("market_research", "market_wait")
    graph.add_conditional_edges(
        "market_wait",
        _after_market_wait,
        {
            "lld_step": "lld_step",
            "hld_step": "hld_step",
            "market_wait": "market_wait",
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
        "phase": "phase0",
        "design_track": "unset",
        "design_step": 0,
        "ready_for_design": False,
        "ready_to_advance": False,
        "design_ready_to_approve": False,
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
        "tradeoff_ledger": "",
        "scale_estimates": "",
        "api_contracts": "",
        "communication_schemes": "",
        "fmea_notes": "",
        "resume_after_market": False,
        "stay_on_interrupt": False,
    }
