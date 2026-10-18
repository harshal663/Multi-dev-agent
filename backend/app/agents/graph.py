from __future__ import annotations
from langgraph.graph import StateGraph, END
from app.agents.state import DevTeamState
from app.agents.nodes import architect_node, coder_node, reviewer_node, qa_node
from app.config import settings


def _intake(state: DevTeamState) -> dict:
    return {"max_cycles": state.get("max_cycles") or settings.max_review_cycles}


def _route_intake(state: DevTeamState) -> str:
    if state.get("edit_suggestion") and state.get("files"):
        return "coder"
    return "architect"


def _route_after_reviewer(state: DevTeamState) -> str:
    blockers = [n for n in state.get("review_notes", []) if n.get("severity") == "blocker"]
    if blockers and state.get("cycle", 0) < state.get("max_cycles", settings.max_review_cycles):
        return "coder"
    return "qa"


def _route_after_qa(state: DevTeamState) -> str:
    if state.get("qa_passed"):
        return END
    if state.get("cycle", 0) >= state.get("max_cycles", settings.max_review_cycles):
        return END
    return "coder"


def build_graph():
    graph = StateGraph(DevTeamState)

    graph.add_node("intake", _intake)
    graph.add_node("architect", architect_node)
    graph.add_node("coder", coder_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("qa", qa_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", _route_intake, {"architect": "architect", "coder": "coder"})
    graph.add_edge("architect", "coder")
    graph.add_edge("coder", "reviewer")
    graph.add_conditional_edges("reviewer", _route_after_reviewer, {"coder": "coder", "qa": "qa"})
    graph.add_conditional_edges("qa", _route_after_qa, {"coder": "coder", END: END})

    return graph.compile()


# Compiled once at import time; nodes are stateless functions so this is safe to share.
dev_team_graph = build_graph()
