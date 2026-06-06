"""Shared graph state for use mode."""
from __future__ import annotations

from typing import Any, TypedDict

from app.logging_utils import AuditTrail


class UseState(TypedDict, total=False):
    run_id: str
    audit: AuditTrail

    # Inputs
    copilot_id: str
    copilot_config: dict[str, Any]
    document: dict[str, Any]

    # Reviewer output and citation-verification state (Loop 4)
    findings: list[dict[str, Any]]
    pending_indices: list[int]  # findings that still need a verified citation
    cite_attempts: dict[int, int]  # per-finding citation attempts (1-indexed)
    last_verifier_feedback: dict[int, dict[str, Any]]  # per-finding rejection payload
    citation_rejections: int  # cumulative for the summary

    # Drafter <-> Critic state (Loop 5)
    drafted_findings: list[dict[str, Any]]
    pending_draft_indices: list[int]  # findings still needing a draft revision
    draft_attempts: dict[int, int]
    last_critic_feedback: dict[int, str]
    draft_critiques: int  # cumulative for the summary

    # Output
    summary: dict[str, Any]
