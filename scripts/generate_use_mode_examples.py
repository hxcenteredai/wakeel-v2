"""Regenerate the build- and use-mode input/output examples and the
committed sample log.

Deterministic (OFFLINE_MODE=true) so the committed evidence is reproducible
without any LLM credentials. Run from the repo root:

    OFFLINE_MODE=true python3 scripts/generate_use_mode_examples.py

The outputs in ``output_examples/`` are the **full /run response envelope**
(``status``, ``agents``, ``trace_id``, ``log_file``,
``execution_time_seconds`` plus the PRD §9 payload spread underneath) so
the committed examples match the live API verbatim. Non-deterministic
fields (run/trace ids, timestamps, wall-clock seconds) are normalised to
stable placeholders for diff-friendly review.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# Force offline before importing the LLM-touching modules.
os.environ.setdefault("OFFLINE_MODE", "true")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.config import LOG_DIR  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402

BUILD_INPUTS = sorted((_ROOT / "input_examples" / "build_mode").glob("*.json"))
USE_INPUTS = [
    _ROOT / "input_examples" / "use_mode" / "01_aggressive_vendor_nda.json",
    _ROOT / "input_examples" / "use_mode" / "02_balanced_partner_nda.json",
    _ROOT / "input_examples" / "use_mode" / "03_data_broker_nda.json",
]
BUILD_OUT_DIR = _ROOT / "output_examples" / "build_mode"
USE_OUT_DIR = _ROOT / "output_examples" / "use_mode"
SAMPLES_DIR = _ROOT / "logs" / "samples"

_DETERMINISTIC_RUN_ID = "<deterministic-offline-run>"
_DETERMINISTIC_TRACE_ID = "<deterministic-offline-trace>"
_DETERMINISTIC_LOG_FILE = f"logs/run_{_DETERMINISTIC_TRACE_ID}.jsonl"


def _normalise(payload: dict) -> dict:
    """Strip non-deterministic envelope + §9 fields (ids, timestamps, wall-clock).

    Keeps the response *shape* stable across runs so a reviewer diffing
    output_examples/ commits to commits sees only meaningful changes.
    """
    payload = dict(payload)

    # Envelope-level non-determinism.
    if "trace_id" in payload:
        payload["trace_id"] = _DETERMINISTIC_TRACE_ID
    if "log_file" in payload:
        payload["log_file"] = _DETERMINISTIC_LOG_FILE
    if "execution_time_seconds" in payload:
        # Field is present + a number, but the actual value is wall-clock-dependent.
        payload["execution_time_seconds"] = 0.0

    # Inner §9 non-determinism.
    payload["run_id"] = _DETERMINISTIC_RUN_ID
    audit = []
    for entry in payload.get("audit_trail", []):
        e = dict(entry)
        e.pop("timestamp", None)
        if "run_id" in e:
            e["run_id"] = _DETERMINISTIC_RUN_ID
        audit.append(e)
    payload["audit_trail"] = audit
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_endpoint(client: TestClient, body: dict) -> dict:
    """Invoke POST /run and return the parsed envelope (success-path only)."""
    resp = client.post("/run", json=body)
    if resp.status_code != 200:
        raise SystemExit(
            f"[gen] /run returned {resp.status_code} for body={body!r}: {resp.text}"
        )
    return resp.json()


def main() -> int:
    BUILD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    USE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    client = TestClient(app)

    # --- 1. Build-mode examples ------------------------------------------
    first_copilot_id: str | None = None
    for input_path in BUILD_INPUTS:
        body = json.loads(input_path.read_text(encoding="utf-8"))
        response = _run_endpoint(client, body)
        if first_copilot_id is None:
            first_copilot_id = response.get("copilot_id")

        out_path = BUILD_OUT_DIR / (input_path.stem + ".out.json")
        _write_json(out_path, _normalise(response))
        loops = sorted({e.get("loop") for e in response["audit_trail"] if e.get("loop")})
        print(
            f"[gen] build {input_path.name} → {out_path.name}: "
            f"agents={len(response.get('agents', []))}, loops={loops}, "
            f"copilot={response.get('copilot_id', '?')}"
        )

    if not first_copilot_id:
        print("[gen] FATAL: build runs produced no copilot_id")
        return 2

    # --- 2. Use-mode examples (share the first build's copilot) ---------
    for input_path in USE_INPUTS:
        case = json.loads(input_path.read_text(encoding="utf-8"))
        case["copilot_id"] = first_copilot_id
        # Persist the linked copilot_id back into the committed input file.
        input_path.write_text(
            json.dumps(case, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        response = _run_endpoint(client, case)
        out_path = USE_OUT_DIR / (input_path.stem + ".out.json")
        _write_json(out_path, _normalise(response))
        summary = response.get("summary", {})
        print(
            f"[gen] use   {input_path.name} → {out_path.name}: "
            f"{len(response.get('findings', []))} findings, "
            f"rejections={summary.get('citation_rejections', 0)}, "
            f"critiques={summary.get('draft_critiques', 0)}"
        )

    # --- 3. Canonical sample log for Loops 4 + 5 (PRD §17 evidence) -----
    # Re-run example 01 once to get a fresh per-run log we can copy out.
    case01 = json.loads(USE_INPUTS[0].read_text(encoding="utf-8"))
    case01["copilot_id"] = first_copilot_id
    rerun = _run_endpoint(client, case01)
    src_log = LOG_DIR / f"run_{rerun.get('run_id')}.jsonl"
    sample_target = SAMPLES_DIR / "use_mode_run_loops_4_5.jsonl"
    if src_log.exists():
        shutil.copyfile(src_log, sample_target)
        print(f"[gen] wrote canonical sample log → {sample_target.relative_to(_ROOT)}")
    else:
        print(f"[gen] WARNING: expected run log at {src_log} but it was not found")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
