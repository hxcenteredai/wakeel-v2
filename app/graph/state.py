"""Shared graph state for build mode."""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from app.logging_utils import AuditTrail


class BuildState(TypedDict, total=False):
    run_id: str
    audit: AuditTrail

    # Intake / interviewer
    intake: dict[str, Any]
    interviewer_result: dict[str, Any]
    interviewer_response: str
    clarification_round: int
    pending_clarification: bool

    # Debate (Loop 1)
    debate_log: list[dict[str, Any]]
    debate_rounds: int

    # Architect (Loop 3 escalation lives here)
    architect_result: dict[str, Any]
    config_outline: dict[str, Any]

    # Builder / Validator (Loop 2)
    copilot_id: str
    copilot_config: dict[str, Any]
    validation: dict[str, Any]
    validation_iteration: int
