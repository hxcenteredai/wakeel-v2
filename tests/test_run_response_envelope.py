"""Submission-evaluator response envelope contract tests for POST /run.

The submission's automated evaluator expects every /run response to carry a
small set of top-level fields *alongside* the existing PRD §9 payload:

  - status: "success" | "error"
  - agents: [{name, role}, ...]  (sourced from metadata.json)
  - trace_id: stable correlation id (mirrors graph.run_id on success)
  - log_file: "logs/run_<trace_id>.jsonl"
  - execution_time_seconds: wall-clock float

And on failure, a structured error body instead of the bare FastAPI default
``{"detail": "..."}``:

  {"status":"error","error":{"type":"...","message":"...","recoverable":bool},
   "trace_id":"...","log_file":"..."}

These tests pin the envelope contract so future changes can't silently break
the evaluator. Each test names the PO-spec invariant it pins.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.helpers import (
    assert_valid_build_response,
    assert_valid_use_response,
    build_payload,
    fresh_copilot,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_METADATA_PATH = _REPO_ROOT / "metadata.json"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOG_FILE_RE = re.compile(r"^logs/run_[0-9a-f-]{36}\.jsonl$")


# --- Success-envelope contract -----------------------------------------------


def test_build_success_envelope_has_all_required_fields(api):
    """PO-spec: a successful /run mode=build response carries the 5 evaluator
    fields at the top level, alongside the existing PRD §9 payload."""
    resp = api.post("/run", json=build_payload("Build me an NDA reviewer", "en"))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # PRD §9 payload preserved verbatim (existing helper).
    assert_valid_build_response(body)

    # PO-spec envelope fields.
    assert body["status"] == "success"
    assert isinstance(body["agents"], list) and body["agents"], "agents must be non-empty"
    assert isinstance(body["trace_id"], str) and body["trace_id"], "trace_id must be set"
    assert isinstance(body["log_file"], str) and body["log_file"], "log_file must be set"
    assert isinstance(body["execution_time_seconds"], (int, float))
    assert body["execution_time_seconds"] >= 0, "wall-clock must be non-negative"


def test_use_success_envelope_has_all_required_fields(api):
    """Same envelope contract holds for mode=use responses."""
    cp_id = fresh_copilot(api)
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": cp_id,
            "document": {"type": "text", "title": "Test NDA", "content": "Vendor NDA body text."},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert_valid_use_response(body)
    assert body["status"] == "success"
    assert isinstance(body["agents"], list) and body["agents"]
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert isinstance(body["log_file"], str) and body["log_file"]
    assert isinstance(body["execution_time_seconds"], (int, float))


def test_agents_field_matches_metadata_json(api):
    """PO-spec: agents are sourced from metadata.json. The /run response must
    surface the same set of {name, role} pairs the submission metadata
    declares — single source of truth."""
    resp = api.post("/run", json=build_payload("Build me an NDA reviewer", "en"))
    assert resp.status_code == 200
    body = resp.json()

    declared = json.loads(_METADATA_PATH.read_text(encoding="utf-8"))["agents"]
    declared_pairs = {(a["name"], a["role"]) for a in declared}
    response_pairs = {(a["name"], a["role"]) for a in body["agents"]}
    assert response_pairs == declared_pairs, (
        f"/run agents drift from metadata.json: "
        f"only-in-response={response_pairs - declared_pairs}, "
        f"only-in-metadata={declared_pairs - response_pairs}"
    )


def test_trace_id_equals_inner_run_id_on_success(api):
    """trace_id mirrors the graph's authoritative run_id on success so the
    log_file path points at the file run_build / run_use actually wrote to."""
    resp = api.post("/run", json=build_payload("Build me an NDA reviewer", "en"))
    body = resp.json()
    assert body["trace_id"] == body["run_id"], (
        "envelope trace_id must equal graph run_id on success"
    )


def test_log_file_path_format(api):
    """log_file follows the canonical ``logs/run_<trace_id>.jsonl`` shape."""
    resp = api.post("/run", json=build_payload("Build me an NDA reviewer", "en"))
    body = resp.json()
    assert _LOG_FILE_RE.match(body["log_file"]), body["log_file"]
    assert body["log_file"].endswith(f"{body['trace_id']}.jsonl")


def test_execution_time_seconds_reflects_wall_clock(api):
    """execution_time_seconds is wall-clock-ish — positive, finite, and
    bounded by a sane ceiling even in offline mode."""
    resp = api.post("/run", json=build_payload("Build me an NDA reviewer", "en"))
    body = resp.json()
    elapsed = body["execution_time_seconds"]
    assert isinstance(elapsed, (int, float))
    assert 0.0 <= elapsed < 600.0, f"unexpected execution_time_seconds: {elapsed}"


# --- Error-envelope contract -------------------------------------------------


def test_build_validation_error_returns_structured_envelope(api):
    """Missing workflow_description must surface the structured error body
    (status, error.type, error.message, error.recoverable, trace_id, log_file).

    HTTP status code is preserved (422) so existing client code keeps working.
    """
    resp = api.post("/run", json={"mode": "build", "intake": {"workflow_description": "  "}})
    assert resp.status_code == 422, resp.text
    body = resp.json()

    assert body["status"] == "error"
    err = body["error"]
    assert err["type"] == "validation_error"
    assert "workflow_description" in err["message"]
    assert err["recoverable"] is True

    # trace_id + log_file are present even though no run actually started.
    assert _UUID_RE.match(body["trace_id"]), body["trace_id"]
    assert _LOG_FILE_RE.match(body["log_file"]), body["log_file"]


def test_use_unknown_copilot_returns_not_found_envelope(api):
    """404 unknown copilot must surface error.type='not_found' with the
    original detail preserved in error.message."""
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": "cp_does_not_exist_xxx",
            "document": {"type": "text", "title": "t", "content": "x"},
        },
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()

    assert body["status"] == "error"
    assert body["error"]["type"] == "not_found"
    assert "cp_does_not_exist_xxx" in body["error"]["message"] or "unknown" in body["error"]["message"].lower()
    assert body["error"]["recoverable"] is True


def test_use_empty_document_returns_validation_error_envelope(api):
    """Empty document content must surface error.type='validation_error'."""
    cp_id = fresh_copilot(api)
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": cp_id,
            "document": {"type": "text", "title": "empty", "content": "   "},
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["type"] == "validation_error"
    assert body["error"]["recoverable"] is True


def test_legacy_detail_field_preserved_for_back_compat(api):
    """Existing clients reading ``response.json()['detail']`` keep working —
    the structured envelope ADDS ``error.message`` without removing ``detail``.

    This is a transition affordance; can be removed once the submission
    evaluator confirms the new envelope is what it expects."""
    resp = api.post("/run", json={"mode": "build", "intake": {"workflow_description": ""}})
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("detail"), "legacy 'detail' field must remain present for back-compat"
    assert body["detail"] == body["error"]["message"], (
        "detail (legacy) must equal error.message (new envelope)"
    )


# --- Cross-cutting -----------------------------------------------------------


def test_status_field_is_present_on_every_run_response(api):
    """Whether success or failure, every /run response has a top-level
    ``status`` field — the evaluator can branch on this without inspecting
    the HTTP code."""
    for payload, expected_status in [
        (build_payload("Build me an NDA reviewer", "en"), "success"),
        ({"mode": "build", "intake": {"workflow_description": ""}}, "error"),
    ]:
        resp = api.post("/run", json=payload)
        body = resp.json()
        assert body.get("status") == expected_status, (payload, body)
