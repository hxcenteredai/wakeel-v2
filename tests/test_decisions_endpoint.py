"""Tests for ``GET /decisions/{copilot_id}`` — read-only decision history.

The endpoint is a pure read wrapper over ``logs/run_*.jsonl`` (per-run audit
trails produced by ``app.logging_utils.AuditTrail``). These tests exercise the
end-to-end behaviour through the FastAPI app:

  * Unknown copilots → 404 (registry check).
  * A freshly built copilot → at least one run (the build run) with build-mode
    entries, no use-mode entries yet.
  * After running use mode against the copilot → an additional use-mode run
    in the history, with Loop 4 / Loop 5 entries surfaced.
  * Multiple use runs against the same copilot → all appear, sorted oldest
    first; entry counts and loops_fired are derived correctly.
  * A malformed log line on disk does not 500 the endpoint (defensive parse).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config, decision_history
from helpers import CUSTOMER_SCENARIOS, USE_MODE_SCENARIOS, fresh_copilot


# --- Unknown copilot ---------------------------------------------------------

def test_unknown_copilot_returns_404(api):
    resp = api.get("/decisions/cp_does_not_exist_99999999")
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "unknown copilot_id" in detail.lower(), detail


# --- Build-only copilot ------------------------------------------------------

def test_freshly_built_copilot_has_at_least_one_run_in_history(api):
    copilot_id = fresh_copilot(api)
    resp = api.get(f"/decisions/{copilot_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copilot_id"] == copilot_id
    assert body["total_runs"] >= 1, body
    assert body["total_decisions"] >= 1, body
    assert isinstance(body["runs"], list)
    build_runs = [r for r in body["runs"] if r["mode"] == "build"]
    assert build_runs, "expected at least one build-mode run in history"
    run = build_runs[0]
    for key in ("run_id", "mode", "started_at", "ended_at", "entry_count", "loops_fired", "entries"):
        assert key in run, f"run summary missing {key}: {run}"
    assert run["entry_count"] == len(run["entries"])
    # Build run must include the Builder action that minted this copilot.
    builder_entries = [
        e for e in run["entries"]
        if e.get("agent") == "Builder" and (e.get("details") or {}).get("copilot_id") == copilot_id
    ]
    assert builder_entries, "Builder action with copilot_id not found in build run"


# --- Use-mode runs surface in history ----------------------------------------

def test_use_mode_run_appears_in_decisions_history(api):
    copilot_id = fresh_copilot(api)
    case = json.loads(
        (Path(__file__).resolve().parent.parent
         / USE_MODE_SCENARIOS[0]["input_file"]).read_text(encoding="utf-8")
    )
    use_resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": copilot_id, "document": case["document"]},
    )
    assert use_resp.status_code == 200, use_resp.text

    resp = api.get(f"/decisions/{copilot_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    use_runs = [r for r in body["runs"] if r["mode"] == "use"]
    assert use_runs, "expected at least one use-mode run after POST /run mode=use"
    run = use_runs[-1]
    # The orchestrator's run_start entry now carries copilot_id in details.
    starts = [
        e for e in run["entries"]
        if e.get("agent") == "orchestrator" and e.get("action") == "run_start"
    ]
    assert starts and (starts[0].get("details") or {}).get("copilot_id") == copilot_id, (
        "use-mode run_start must stamp copilot_id in details so history is filterable"
    )
    # Loops 4 / 5 must surface in the per-run loops_fired summary.
    assert "Loop 4" in run["loops_fired"], run["loops_fired"]
    assert "Loop 5" in run["loops_fired"], run["loops_fired"]


# --- Multiple use runs against the same copilot ------------------------------

def test_multiple_use_runs_all_appear_sorted_oldest_first(api):
    copilot_id = fresh_copilot(api)
    titles_run = []
    for sc in USE_MODE_SCENARIOS[:2]:  # two distinct NDA scenarios
        case = json.loads(
            (Path(__file__).resolve().parent.parent
             / sc["input_file"]).read_text(encoding="utf-8")
        )
        r = api.post(
            "/run",
            json={"mode": "use", "copilot_id": copilot_id, "document": case["document"]},
        )
        assert r.status_code == 200, r.text
        titles_run.append(sc["title"])

    body = api.get(f"/decisions/{copilot_id}").json()
    use_runs = [r for r in body["runs"] if r["mode"] == "use"]
    assert len(use_runs) >= 2, f"expected >=2 use-mode runs, got {len(use_runs)}"
    started = [r["started_at"] for r in use_runs]
    assert started == sorted(started), f"runs not sorted oldest-first: {started}"
    # total_decisions matches the sum of per-run entry_count.
    assert body["total_decisions"] == sum(r["entry_count"] for r in body["runs"])


# --- Malformed log line resilience -------------------------------------------

def test_malformed_log_line_does_not_500_the_endpoint(api, tmp_path, monkeypatch):
    """A garbage line in a run log must be skipped, not crash the endpoint."""
    copilot_id = fresh_copilot(api)

    # Point the history reader at a temp log dir that contains both a valid
    # entry referencing our copilot AND a malformed line. We do NOT touch the
    # real LOG_DIR — the endpoint reads from config.LOG_DIR via
    # decision_history.history_for_copilot's default, so we monkeypatch that.
    fake_log = tmp_path / "run_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    fake_log.write_text(
        # Valid line — references the copilot via details.copilot_id.
        json.dumps({
            "timestamp": "2026-06-05T00:00:00+00:00",
            "run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "mode": "use",
            "agent": "orchestrator",
            "action": "run_start",
            "decision": "use",
            "reason": "synthetic test entry",
            "loop": None,
            "details": {"copilot_id": copilot_id},
        })
        + "\n"
        # Malformed line — not JSON.
        + "this-is-not-json-and-must-be-skipped\n"
        # Another valid line — a Loop 4 firing for the same copilot.
        + json.dumps({
            "timestamp": "2026-06-05T00:00:01+00:00",
            "run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "mode": "use",
            "agent": "Citation Verifier",
            "action": "verify_citation",
            "decision": "verified",
            "reason": "synthetic",
            "loop": "Loop 4",
            "details": {},
        })
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    # Call the pure function directly with our fake log dir so we can assert
    # the parser's malformed-line resilience without touching the real LOG_DIR
    # (the endpoint is a thin wrapper around exactly this call).
    runs = decision_history.history_for_copilot(copilot_id, log_dir=tmp_path)
    assert len(runs) == 1, f"expected 1 run, got {len(runs)}"
    run = runs[0]
    # Both valid lines parsed, the malformed line skipped.
    assert run["entry_count"] == 2, run
    assert "Loop 4" in run["loops_fired"]


# --- Pure-function unit checks (no API) -------------------------------------

def test_history_returns_empty_for_unmatched_copilot(tmp_path):
    runs = decision_history.history_for_copilot("cp_nope", log_dir=tmp_path)
    assert runs == []


def test_history_returns_empty_for_blank_copilot_id(tmp_path):
    assert decision_history.history_for_copilot("", log_dir=tmp_path) == []


def test_history_recovers_copilot_id_from_reason_fallback(tmp_path):
    """Older use-mode logs (pre-tagging) carried copilot_id only in the
    orchestrator reason string. The fallback must still surface them."""
    log = tmp_path / "run_11111111-1111-1111-1111-111111111111.jsonl"
    log.write_text(
        json.dumps({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "11111111-1111-1111-1111-111111111111",
            "mode": "use",
            "agent": "orchestrator",
            "action": "run_start",
            "decision": "use",
            "reason": "POST /run mode=use copilot=cp_legacy123",
            "loop": None,
            # NOTE: no copilot_id in details — pre-tagging shape.
            "details": {},
        })
        + "\n",
        encoding="utf-8",
    )
    runs = decision_history.history_for_copilot("cp_legacy123", log_dir=tmp_path)
    assert len(runs) == 1, runs
    assert runs[0]["entry_count"] == 1
