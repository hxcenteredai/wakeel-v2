"""Build-mode graph: Interviewer -> Debate (Loop 1) -> Architect
(-> Loop 3 clarify) -> Builder <-> Validator (Loop 2) -> END.

Hard caps prevent non-convergence:
  MAX_DEBATE_ROUNDS   = 3   (Loop 1)
  MAX_CLARIFICATIONS  = 2   (Loop 3)
  MAX_VALIDATIONS     = 3   (Loop 2)
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from app import copilot_registry
from app.agents import build_agents as agents
from app.graph.state import BuildState
from app.logging_utils import AuditTrail

MAX_DEBATE_ROUNDS = 3
MAX_CLARIFICATIONS = 2
MAX_VALIDATIONS = 3


# --- Nodes --------------------------------------------------------------------

def interviewer_node(state: BuildState) -> BuildState:
    audit = state["audit"]
    # If the Architect escalated (Loop 3), this is a re-interview: advance the
    # clarification counter and clear the pending flag.
    if state.get("pending_clarification"):
        state["clarification_round"] = state.get("clarification_round", 0) + 1
        state["pending_clarification"] = False
    clarification_round = state.get("clarification_round", 0)
    result = agents.interviewer(audit, state["intake"], clarification_round)

    # Merge any extracted fields back into intake so downstream agents see them.
    intake = dict(state["intake"])
    for key in ("org_id", "document_type", "risk_appetite", "language"):
        if result.get(key):
            intake[key] = result[key]
    intake["needs_clarification"] = bool(result.get("needs_clarification"))

    state["intake"] = intake
    state["interviewer_result"] = result
    state["interviewer_response"] = result.get("response_to_user", "")
    return state


def debate_node(state: BuildState) -> BuildState:
    audit = state["audit"]
    debate = list(state.get("debate_log", []))
    round_no = state.get("debate_rounds", 0) + 1

    arg_a = agents.debater(audit, "A", state["intake"], round_no, debate)
    arg_b = agents.debater(audit, "B", state["intake"], round_no, debate)
    debate.append({"round": round_no, "A": arg_a, "B": arg_b})

    state["debate_log"] = debate
    state["debate_rounds"] = round_no
    return state


def architect_node(state: BuildState) -> BuildState:
    audit = state["audit"]
    result = agents.architect(
        audit,
        state["intake"],
        state.get("debate_log", []),
        state.get("debate_rounds", 0),
        state.get("clarification_round", 0),
    )
    state["architect_result"] = result
    if result.get("config_outline"):
        state["config_outline"] = result["config_outline"]

    # Decide pending clarification here (node), so the counter survives routing.
    decision = result.get("decision", "synthesize")
    state["pending_clarification"] = (
        decision == "needs_clarification"
        and state.get("clarification_round", 0) < MAX_CLARIFICATIONS
    )
    return state


def builder_node(state: BuildState) -> BuildState:
    audit = state["audit"]
    copilot_id = state.get("copilot_id") or f"cp_{uuid.uuid4().hex[:8]}"
    config = agents.builder(
        audit, state["intake"], state.get("config_outline", {}), copilot_id
    )
    state["copilot_id"] = copilot_id
    state["copilot_config"] = config
    return state


def validator_node(state: BuildState) -> BuildState:
    audit = state["audit"]
    iteration = state.get("validation_iteration", 0) + 1
    result = agents.validator(audit, state.get("copilot_config", {}), iteration)
    state["validation"] = result
    state["validation_iteration"] = iteration
    return state


# --- Routers ------------------------------------------------------------------

def route_after_interviewer(state: BuildState) -> str:
    # Returning from a Loop-3 clarification (debate already ran) -> go synthesize.
    if state.get("debate_rounds", 0) > 0:
        return "architect"
    return "debate"


def route_after_architect(state: BuildState) -> str:
    result = state.get("architect_result", {})
    decision = result.get("decision", "synthesize")

    if state.get("pending_clarification"):
        return "interviewer"

    if (
        decision == "request_more_debate"
        and state.get("debate_rounds", 0) < MAX_DEBATE_ROUNDS
    ):
        return "debate"

    return "builder"


def route_after_validator(state: BuildState) -> str:
    result = state.get("validation", {})
    if not result.get("passed") and state.get("validation_iteration", 0) < MAX_VALIDATIONS:
        return "builder"
    return END


# --- Graph assembly -----------------------------------------------------------

def build_build_graph():
    g = StateGraph(BuildState)
    g.add_node("interviewer", interviewer_node)
    g.add_node("debate", debate_node)
    g.add_node("architect", architect_node)
    g.add_node("builder", builder_node)
    g.add_node("validator", validator_node)

    g.set_entry_point("interviewer")
    g.add_conditional_edges(
        "interviewer", route_after_interviewer, {"debate": "debate", "architect": "architect"}
    )
    g.add_edge("debate", "architect")
    g.add_conditional_edges(
        "architect",
        route_after_architect,
        {"interviewer": "interviewer", "debate": "debate", "builder": "builder"},
    )
    g.add_edge("builder", "validator")
    g.add_conditional_edges("validator", route_after_validator, {"builder": "builder", END: END})
    return g.compile()


_compiled = None


def get_build_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_build_graph()
    return _compiled


def run_build(intake: dict[str, Any]) -> dict[str, Any]:
    """Execute build mode end-to-end and return the API response payload."""
    run_id = str(uuid.uuid4())
    audit = AuditTrail(run_id, mode="build")
    audit.add(agent="orchestrator", action="run_start", decision="build", reason="POST /run mode=build")

    initial: BuildState = {
        "run_id": run_id,
        "audit": audit,
        "intake": dict(intake),
        "clarification_round": 0,
        "debate_log": [],
        "debate_rounds": 0,
        "validation_iteration": 0,
    }

    graph = get_build_graph()
    final = graph.invoke(initial, config={"recursion_limit": 50})

    copilot_id = final.get("copilot_id", "")
    copilot_config = final.get("copilot_config", {})

    # Persist the config so mode=use can replay it later.
    if copilot_id and copilot_config:
        try:
            copilot_registry.save(copilot_id, copilot_config)
        except Exception as exc:  # pragma: no cover - persistence is best-effort
            audit.add(
                agent="orchestrator",
                action="persist_copilot",
                decision="error",
                reason=f"registry save failed: {exc}",
            )

    audit.add(
        agent="orchestrator",
        action="run_complete",
        decision="ok",
        reason=f"copilot {copilot_id} validated",
    )

    return {
        "run_id": run_id,
        "mode": "build",
        "copilot_id": copilot_id,
        "config": copilot_config,
        "validation_results": final.get("validation", {}),
        "audit_trail": audit.as_list(),
        "interviewer_response": final.get("interviewer_response", ""),
    }
