from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from architect_agent.graph.nodes.spec_interview import spec_interview_node
from architect_agent.graph.nodes.system_design import system_design_node
from architect_agent.graph.state import DesignGraphState

_checkpointer = MemorySaver()
_compiled: CompiledStateGraph | None = None


def _after_interview(state: DesignGraphState) -> Literal["system_design", "__end__"]:
    if state.get("spec_approved"):
        return "system_design"
    return "__end__"


def _after_design(state: DesignGraphState) -> Literal["__end__"]:
    return "__end__"


def build_graph() -> CompiledStateGraph:
    global _compiled
    if _compiled is not None:
        return _compiled

    graph = StateGraph(DesignGraphState)
    graph.add_node("spec_interview", spec_interview_node)
    graph.add_node("system_design", system_design_node)
    graph.add_edge(START, "spec_interview")
    graph.add_conditional_edges(
        "spec_interview",
        _after_interview,
        {"system_design": "system_design", "__end__": END},
    )
    graph.add_conditional_edges(
        "system_design",
        _after_design,
        {"__end__": END},
    )
    _compiled = graph.compile(checkpointer=_checkpointer)
    return _compiled


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
    }
