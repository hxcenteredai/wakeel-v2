"""Use-mode graph: Reviewer -> Citation Verifier (Loop 4) -> Drafter <-> Critic
(Loop 5) -> Synthesis -> END.

Hard caps per PRD §7:
  MAX_CITATION_RETRIES = 3   (Loop 4, per finding)
  MAX_DRAFT_RETRIES    = 3   (Loop 5, per finding)
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents import use_agents as agents
from app.agents.base import as_dict
from app.copilot_registry import load as load_copilot
from app.graph.use_state import UseState
from app.logging_utils import AuditTrail

MAX_CITATION_RETRIES = 3
MAX_DRAFT_RETRIES = 3
DRAFT_RISK_LEVELS = {"high", "medium"}


# --- Nodes --------------------------------------------------------------------

def reviewer_node(state: UseState) -> UseState:
    audit = state["audit"]
    result = agents.reviewer(audit, state["copilot_config"], state["document"], attempt=1)
    # ``agents.reviewer`` already coerces findings to a list[dict]; this second
    # pass is a belt-and-braces guarantee against any future regression.
    findings = [as_dict(f) for f in (result.get("findings") or []) if as_dict(f)]
    state["findings"] = findings
    state["pending_indices"] = list(range(len(findings)))
    state["cite_attempts"] = {i: 1 for i in range(len(findings))}
    state["last_verifier_feedback"] = {}
    state["citation_rejections"] = 0
    state["draft_critiques"] = 0
    state["drafted_findings"] = list(findings)  # placeholder, fills after Loop 5
    state["pending_draft_indices"] = []
    state["draft_attempts"] = {}
    state["last_critic_feedback"] = {}
    return state


def verifier_node(state: UseState) -> UseState:
    """Verify every pending finding's citation. Loop 4 fires per rejection."""
    audit = state["audit"]
    findings = list(state.get("findings", []))
    pending_next: list[int] = []
    rejections = state.get("citation_rejections", 0)

    for i in list(state.get("pending_indices", [])):
        # Guard against the finding itself being a non-dict (live model
        # returned ``[1,2,3]`` for findings, etc.). Skip and surface in audit.
        findings[i] = as_dict(findings[i])
        attempt = state.get("cite_attempts", {}).get(i, 1)
        verdict = agents.citation_verifier(audit, findings[i], attempt)
        # Coerce citation to a dict BEFORE mutating it — live recite responses
        # are the chief source of int/str citations in this position.
        citation = as_dict(findings[i].get("citation"))
        if verdict.get("verified"):
            citation["verified"] = True
            citation["exact_text"] = verdict.get("exact_text")
            citation["verification_attempts"] = attempt
            findings[i]["citation"] = citation
            continue

        rejections += 1
        if attempt < MAX_CITATION_RETRIES:
            # Loop 4 retry: feed candidates back to the Reviewer.
            state.setdefault("last_verifier_feedback", {})[i] = verdict
            pending_next.append(i)
        else:
            # Exhausted retries: keep finding but flag the citation as unverified.
            citation["verified"] = False
            citation["verification_attempts"] = attempt
            findings[i]["citation"] = citation

    state["findings"] = findings
    state["pending_indices"] = pending_next
    state["citation_rejections"] = rejections
    return state


def reviewer_recite_node(state: UseState) -> UseState:
    """Loop-4 re-cite: ask the Reviewer to fix every still-pending finding."""
    audit = state["audit"]
    findings = list(state.get("findings", []))
    cite_attempts = dict(state.get("cite_attempts", {}))
    feedback_map = state.get("last_verifier_feedback", {}) or {}

    for i in state.get("pending_indices", []):
        cite_attempts[i] = cite_attempts.get(i, 1) + 1
        verifier_feedback = feedback_map.get(i, {}) or {}
        findings[i] = as_dict(findings[i])
        revised = agents.reviewer_recite(audit, findings[i], verifier_feedback, cite_attempts[i])
        findings[i] = as_dict(revised, default=findings[i])

    state["findings"] = findings
    state["cite_attempts"] = cite_attempts
    state["last_verifier_feedback"] = {}
    return state


def drafter_node(state: UseState) -> UseState:
    """Initial draft pass over every high/medium finding."""
    audit = state["audit"]
    findings = [as_dict(f) for f in state.get("findings", [])]
    drafted = list(findings)
    draft_attempts: dict[int, int] = {}
    pending_draft: list[int] = []

    for i, finding in enumerate(findings):
        if _risk_level(finding) not in DRAFT_RISK_LEVELS:
            drafted[i] = finding
            continue
        draft = as_dict(agents.counter_proposal_drafter(audit, finding, attempt=1))
        draft_attempts[i] = 1
        drafted[i] = dict(finding)
        # `or ""` defends against the live shape ``{"draft_clause": null}`` —
        # ``.get(k, "")`` only fires the default when the key is MISSING, not
        # when its value is None.
        drafted[i]["counter_proposal"] = draft.get("draft_clause") or ""
        drafted[i]["counter_proposal_iterations"] = 1
        pending_draft.append(i)

    state["drafted_findings"] = drafted
    state["pending_draft_indices"] = pending_draft
    state["draft_attempts"] = draft_attempts
    return state


def _risk_level(finding: dict) -> str:
    """Tolerant accessor: returns lowercased risk string or '' for any shape."""
    risk = finding.get("risk", "") if isinstance(finding, dict) else ""
    if isinstance(risk, str):
        return risk.lower()
    return str(risk).lower()


def critic_node(state: UseState) -> UseState:
    """Loop-5 critique: route bad drafts back to the Drafter."""
    audit = state["audit"]
    drafted = [as_dict(f) for f in state.get("drafted_findings", [])]
    pending_next: list[int] = []
    critic_feedback: dict[int, str] = {}
    critiques = state.get("draft_critiques", 0)

    for i in list(state.get("pending_draft_indices", [])):
        attempt = state.get("draft_attempts", {}).get(i, 1)
        finding = drafted[i]
        draft_payload = {"draft_clause": finding.get("counter_proposal", "")}
        verdict = as_dict(agents.reviewer_critic(audit, finding, draft_payload, attempt))
        if verdict.get("accepted"):
            drafted[i]["counter_proposal_accepted"] = True
            continue
        critiques += 1
        if attempt < MAX_DRAFT_RETRIES:
            critic_feedback[i] = verdict.get("critique", "")
            pending_next.append(i)
        else:
            drafted[i]["counter_proposal_accepted"] = False

    state["drafted_findings"] = drafted
    state["pending_draft_indices"] = pending_next
    state["last_critic_feedback"] = critic_feedback
    state["draft_critiques"] = critiques
    return state


def drafter_revise_node(state: UseState) -> UseState:
    """Loop-5 revise: Drafter re-runs for every rejected draft."""
    audit = state["audit"]
    drafted = [as_dict(f) for f in state.get("drafted_findings", [])]
    draft_attempts = dict(state.get("draft_attempts", {}))
    feedback_map = state.get("last_critic_feedback", {}) or {}

    for i in state.get("pending_draft_indices", []):
        draft_attempts[i] = draft_attempts.get(i, 1) + 1
        draft = as_dict(agents.counter_proposal_drafter(
            audit,
            drafted[i],
            attempt=draft_attempts[i],
            critique=feedback_map.get(i, ""),
        ))
        drafted[i]["counter_proposal"] = draft.get("draft_clause") or ""
        drafted[i]["counter_proposal_iterations"] = draft_attempts[i]

    state["drafted_findings"] = drafted
    state["draft_attempts"] = draft_attempts
    state["last_critic_feedback"] = {}
    return state


def synthesis_node(state: UseState) -> UseState:
    audit = state["audit"]
    findings = [as_dict(f) for f in (state.get("drafted_findings") or state.get("findings", []))]
    summary = agents.synthesis(
        audit,
        findings,
        state.get("citation_rejections", 0),
        state.get("draft_critiques", 0),
    )
    # ``agents.synthesis`` already coerces to a dict; this is a safety net.
    state["summary"] = as_dict(summary)
    return state


# --- Routers ------------------------------------------------------------------

def route_after_verifier(state: UseState) -> str:
    return "reviewer_recite" if state.get("pending_indices") else "drafter"


def route_after_recite(state: UseState) -> str:
    # After a re-cite, re-verify the (now-revised) findings.
    return "verifier"


def route_after_critic(state: UseState) -> str:
    return "drafter_revise" if state.get("pending_draft_indices") else "synthesis"


def route_after_revise(state: UseState) -> str:
    return "critic"


# --- Graph assembly -----------------------------------------------------------

def build_use_graph():
    g = StateGraph(UseState)
    g.add_node("reviewer", reviewer_node)
    g.add_node("verifier", verifier_node)
    g.add_node("reviewer_recite", reviewer_recite_node)
    g.add_node("drafter", drafter_node)
    g.add_node("critic", critic_node)
    g.add_node("drafter_revise", drafter_revise_node)
    g.add_node("synthesis", synthesis_node)

    g.set_entry_point("reviewer")
    g.add_edge("reviewer", "verifier")
    g.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {"reviewer_recite": "reviewer_recite", "drafter": "drafter"},
    )
    g.add_conditional_edges(
        "reviewer_recite",
        route_after_recite,
        {"verifier": "verifier"},
    )
    g.add_edge("drafter", "critic")
    g.add_conditional_edges(
        "critic",
        route_after_critic,
        {"drafter_revise": "drafter_revise", "synthesis": "synthesis"},
    )
    g.add_conditional_edges(
        "drafter_revise",
        route_after_revise,
        {"critic": "critic"},
    )
    g.add_edge("synthesis", END)
    return g.compile()


_compiled = None


def get_use_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_use_graph()
    return _compiled


# --- Public entry -------------------------------------------------------------

def run_use(copilot_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """Execute use mode end-to-end and return the API response payload."""
    copilot_config = load_copilot(copilot_id)
    if copilot_config is None:
        raise ValueError(f"unknown copilot_id: {copilot_id}")

    run_id = str(uuid.uuid4())
    audit = AuditTrail(run_id, mode="use")
    # copilot_id stamped in `details` so per-copilot decision history
    # (GET /decisions/{copilot_id}) can structurally filter use-mode runs
    # without relying on string-parsing the reason field.
    audit.add(
        agent="orchestrator",
        action="run_start",
        decision="use",
        reason=f"POST /run mode=use copilot={copilot_id}",
        details={"copilot_id": copilot_id},
    )

    initial: UseState = {
        "run_id": run_id,
        "audit": audit,
        "copilot_id": copilot_id,
        "copilot_config": copilot_config,
        "document": dict(document),
        "findings": [],
        "pending_indices": [],
        "cite_attempts": {},
        "last_verifier_feedback": {},
        "citation_rejections": 0,
        "drafted_findings": [],
        "pending_draft_indices": [],
        "draft_attempts": {},
        "last_critic_feedback": {},
        "draft_critiques": 0,
        "summary": {},
    }

    graph = get_use_graph()
    final = graph.invoke(initial, config={"recursion_limit": 100})

    findings = final.get("drafted_findings") or final.get("findings", [])
    summary = final.get("summary", {})

    audit.add(
        agent="orchestrator",
        action="run_complete",
        decision="ok",
        reason=(
            f"{len(findings)} findings, "
            f"{final.get('citation_rejections', 0)} citation rejections (Loop 4), "
            f"{final.get('draft_critiques', 0)} draft critiques (Loop 5)"
        ),
    )

    return {
        "run_id": run_id,
        "mode": "use",
        "copilot_id": copilot_id,
        "findings": findings,
        "summary": summary,
        "audit_trail": audit.as_list(),
    }
