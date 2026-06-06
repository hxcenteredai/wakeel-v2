"""Pydantic request/response models for POST /run.

Two layers documented here:

* **PRD §9 payloads** (``BuildResponse`` / ``UseResponse``) — the structured
  result the LangGraph build/use graphs produce. These are documentation-only
  models; the endpoint does **not** wire them as ``response_model=`` because
  FastAPI would strip extra envelope fields below.
* **Submission-evaluator envelope** (``RunSuccessEnvelope`` /
  ``RunErrorEnvelope``) — the full top-level shape returned by ``POST /run``.
  Wraps the PRD §9 payload (spread under the success envelope) and replaces
  the bare FastAPI ``{"detail": "..."}`` error body with a structured form
  on failure.

See ``docs/architecture.md`` §3 and ``README.md`` §13 for the human-readable
contract, and ``tests/test_run_response_envelope.py`` for the executable
contract tests.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class BuildIntake(BaseModel):
    workflow_description: str = Field(..., description="User's description (EN or AR).")
    org_id: Optional[str] = None
    document_type: Optional[str] = "nda"
    risk_appetite: Optional[str] = None
    language: Literal["en", "ar"] = "en"


class UseDocument(BaseModel):
    """Document submitted in use mode (PRD §9)."""

    type: Literal["text"] = "text"
    content: str = Field(..., description="Raw document text (e.g. NDA body).")
    title: Optional[str] = None


class RunRequest(BaseModel):
    mode: Literal["build", "use"]
    # Build mode
    intake: Optional[BuildIntake] = None
    # Use mode
    copilot_id: Optional[str] = None
    document: Optional[UseDocument] = None


class LLMConfigUpdate(BaseModel):
    """Runtime LLM settings editable from the front-end (provider flexibility)."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    default_model: Optional[str] = None
    reasoning_model: Optional[str] = None
    embedding_model: Optional[str] = None
    interviewer_model: Optional[str] = None
    offline: Optional[bool] = None


class BuildResponse(BaseModel):
    run_id: str
    mode: Literal["build"] = "build"
    copilot_id: str
    config: dict[str, Any]
    validation_results: dict[str, Any]
    audit_trail: list[dict[str, Any]]
    interviewer_response: str = ""


class Citation(BaseModel):
    law: str
    article: str
    verified: bool = False
    exact_text: Optional[str] = None
    verification_attempts: int = 1


class Finding(BaseModel):
    clause: str
    risk: Literal["high", "medium", "low"]
    confidence: float
    citation: Citation
    rationale: str
    counter_proposal: Optional[str] = None
    counter_proposal_iterations: int = 0


class UseSummary(BaseModel):
    total_findings: int = 0
    high_risk: int = 0
    medium_risk: int = 0
    low_risk: int = 0
    verified_citations: int = 0
    citation_rejections: int = 0
    draft_critiques: int = 0
    recommendation: str = ""


class UseResponse(BaseModel):
    run_id: str
    mode: Literal["use"] = "use"
    copilot_id: str
    findings: list[Finding]
    summary: UseSummary
    audit_trail: list[dict[str, Any]]


# --- Submission-evaluator envelope -------------------------------------------
#
# The PO mandated a top-level envelope on every /run response. These models
# document the wire shape returned by ``app/api.py:run`` so a downstream
# reader / evaluator knows the exact contract without inferring it from the
# endpoint body. They're not wired as ``response_model=`` (which would strip
# the PRD §9 payload that gets spread under the envelope).


class AgentDescriptor(BaseModel):
    """One entry in the ``agents`` envelope field.

    Sourced from ``metadata.json``'s ``agents`` array — single source of
    truth for the agent inventory across the submission package.
    """

    name: str
    role: str


class ErrorDetail(BaseModel):
    """Body of the ``error`` field on a failed /run response.

    ``type`` enum:

    * ``validation_error`` — 422, the request body / arguments are malformed
    * ``not_found`` — 404, e.g. an unknown ``copilot_id``
    * ``internal_error`` — 5xx, an unhandled server-side failure
    * ``client_error`` — other 4xx
    """

    type: Literal["validation_error", "not_found", "internal_error", "client_error"]
    message: str
    recoverable: bool = Field(
        ...,
        description="True for status < 500 (client can retry with corrected input); false for 5xx.",
    )


class RunSuccessEnvelope(BaseModel):
    """Top-level shape of every successful POST /run response.

    The PRD §9 payload (``BuildResponse`` or ``UseResponse``) is spread at
    top level alongside these envelope fields, so a §9 consumer still finds
    every required field in its expected place.
    """

    status: Literal["success"] = "success"
    agents: list[AgentDescriptor]
    trace_id: str = Field(..., description="Equals the graph's run_id on success.")
    log_file: str = Field(..., description="Canonical 'logs/run_<trace_id>.jsonl' path.")
    execution_time_seconds: float = Field(
        ..., description="Wall-clock seconds the run took, measured at the endpoint."
    )
    # PRD §9 payload (spread under this envelope at the wire level):
    run_id: str
    mode: Literal["build", "use"]
    # Build-mode keys (present when mode == "build"):
    copilot_id: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    validation_results: Optional[dict[str, Any]] = None
    interviewer_response: Optional[str] = None
    # Use-mode keys (present when mode == "use"):
    findings: Optional[list[Finding]] = None
    summary: Optional[UseSummary] = None
    # Common:
    audit_trail: list[dict[str, Any]]


class RunErrorEnvelope(BaseModel):
    """Top-level shape of every failed POST /run response.

    Replaces FastAPI's default ``{"detail": "..."}`` body. The original HTTP
    status code is preserved on the response (422 / 404 / 5xx).
    """

    status: Literal["error"] = "error"
    error: ErrorDetail
    trace_id: str = Field(
        ..., description="UUID generated at the endpoint so even pre-run failures are traceable."
    )
    log_file: str = Field(..., description="Canonical 'logs/run_<trace_id>.jsonl' path.")
    detail: str = Field(
        ...,
        description="Back-compat mirror of error.message; clients reading the legacy FastAPI shape still work.",
    )
