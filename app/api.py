"""FastAPI application exposing POST /run (port 8000)."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app import copilot_registry, decision_history, llm
from app.graph.build_graph import run_build
from app.graph.use_graph import run_use
from app.schemas import LLMConfigUpdate, RunRequest

app = FastAPI(title="Wakeel — Regulatory Agent Factory", version="1.0.0")


# --- Submission-evaluator response envelope helpers ---------------------------
#
# The submission's automated evaluator expects every /run response to carry a
# small set of top-level fields (status, agents, trace_id, log_file,
# execution_time_seconds) alongside the existing PRD §9 payload, plus a
# structured error body on failure. These helpers keep that envelope logic in
# one place so the graph code (run_build / run_use) stays single-responsibility.

_METADATA_PATH = Path(__file__).resolve().parent.parent / "metadata.json"
_AGENTS_CACHE: list[dict[str, str]] | None = None


def _agents_from_metadata() -> list[dict[str, str]]:
    """Return the agent inventory in ``[{name, role}, ...]`` form.

    Sourced from ``metadata.json`` so the inventory the evaluator sees on
    /run responses is the same one declared in the submission metadata
    file — no duplication, single source of truth.
    """
    global _AGENTS_CACHE
    if _AGENTS_CACHE is None:
        try:
            data = json.loads(_METADATA_PATH.read_text(encoding="utf-8"))
            _AGENTS_CACHE = [
                {"name": str(a.get("name", "")), "role": str(a.get("role", ""))}
                for a in data.get("agents", [])
                if a.get("name")
            ]
        except (OSError, json.JSONDecodeError):
            _AGENTS_CACHE = []
    return _AGENTS_CACHE


def _error_type_for_status(status_code: int) -> str:
    """Map an HTTP status code to a stable error-type string for the envelope."""
    if status_code == 422:
        return "validation_error"
    if status_code == 404:
        return "not_found"
    if status_code >= 500:
        return "internal_error"
    return "client_error"


def _log_file_for(trace_id: str) -> str:
    """Conventional log-file path for a given trace/run id (per AuditTrail)."""
    return f"logs/run_{trace_id}.jsonl"


def _error_envelope(trace_id: str, status_code: int, message: str) -> dict[str, Any]:
    """Render the failure envelope. Includes legacy ``detail`` for back-compat."""
    return {
        "status": "error",
        "error": {
            "type": _error_type_for_status(status_code),
            "message": message,
            "recoverable": status_code < 500,
        },
        "trace_id": trace_id,
        "log_file": _log_file_for(trace_id),
        # Legacy FastAPI default field; kept so existing clients that read
        # `response.json()["detail"]` continue to work while we transition to
        # the structured envelope above.
        "detail": message,
    }


# --- Endpoints ----------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", **llm.current_config()}


@app.get("/config")
def get_config() -> dict:
    """Current LLM configuration (API key masked)."""
    return llm.current_config()


@app.post("/config")
def set_config(update: LLMConfigUpdate) -> dict:
    """Change the API key / endpoint / models at runtime (front-end provider switch)."""
    models = {
        "standard": update.default_model,
        "reasoning": update.reasoning_model,
        "embedding": update.embedding_model,
    }
    models = {tier: name for tier, name in models.items() if name}
    return llm.reconfigure(
        api_key=update.api_key,
        base_url=update.base_url,
        models=models or None,
        interviewer_model=update.interviewer_model,
        offline=update.offline,
    )


@app.get("/copilots")
def list_copilots() -> dict:
    """List copilots currently registered (built via mode=build)."""
    return {"copilots": copilot_registry.list_copilots()}


@app.get("/decisions/{copilot_id}")
def get_decisions(copilot_id: str) -> dict:
    """Return the full decision history for a copilot.

    Read-only wrapper around the existing ``logs/run_*.jsonl`` audit trails:
    scans every run, filters to runs that reference ``copilot_id``, groups
    the entries per run, and returns a structured response with per-run
    metadata (mode, timestamps, loops fired) alongside the raw entries.

    Pure file read. No new database, no schema, no new dependencies.
    """
    if copilot_registry.load(copilot_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown copilot_id: {copilot_id}")
    runs = decision_history.history_for_copilot(copilot_id)
    return {
        "copilot_id": copilot_id,
        "total_runs": len(runs),
        "total_decisions": sum(r["entry_count"] for r in runs),
        "runs": runs,
    }


@app.post("/run")
def run(req: RunRequest):
    """Execute a build or use run and wrap the result in the submission envelope.

    Returns the existing PRD §9 payload (``run_id``, ``copilot_id``,
    ``audit_trail``, …) **merged** under a top-level envelope that adds
    ``status``, ``agents`` (from ``metadata.json``), ``trace_id``,
    ``log_file``, and ``execution_time_seconds`` for the submission's
    automated evaluator. On failure, returns a structured error envelope
    while preserving the original HTTP status code.
    """
    # Generate a request-scoped trace id upfront so even pre-run failures
    # (validation errors, unknown copilots) can be traced.
    endpoint_trace_id = str(uuid.uuid4())
    started_at = time.perf_counter()

    try:
        if req.mode == "build":
            if not req.intake or not req.intake.workflow_description.strip():
                raise HTTPException(
                    status_code=422,
                    detail="build mode requires intake.workflow_description",
                )
            inner = run_build(req.intake.model_dump())
        elif req.mode == "use":
            if not req.copilot_id:
                raise HTTPException(status_code=422, detail="use mode requires copilot_id")
            if not req.document or not req.document.content.strip():
                raise HTTPException(status_code=422, detail="use mode requires document.content")
            try:
                inner = run_use(req.copilot_id, req.document.model_dump())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
        else:
            raise HTTPException(status_code=422, detail=f"unknown mode: {req.mode}")
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(endpoint_trace_id, exc.status_code, str(exc.detail)),
        )

    # Success — use the graph's authoritative run_id as the trace id so the
    # envelope's log_file points at the file run_build / run_use actually
    # wrote to.
    resolved_trace_id = str(inner.get("run_id") or endpoint_trace_id)
    elapsed_seconds = round(time.perf_counter() - started_at, 6)

    return {
        "status": "success",
        "agents": _agents_from_metadata(),
        "trace_id": resolved_trace_id,
        "log_file": _log_file_for(resolved_trace_id),
        "execution_time_seconds": elapsed_seconds,
        **inner,
    }
