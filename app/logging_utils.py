"""Structured JSONL logging for the audit trail and LLM call records.

Two logical streams, both JSONL files under LOG_DIR:
  - llm_calls.jsonl   : one record per LLM call (SOW section 6, property 7)
  - run audit trails  : every agent action / loop iteration, written per-run
                        and also returned in the API response.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonl_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# --- LLM call logging (SOW section 6, property 7) ---

_LLM_LOG = config.LOG_DIR / "llm_calls.jsonl"

_py_logger = logging.getLogger("wakeel.llm")
if not _py_logger.handlers:
    logging.basicConfig(level=logging.INFO)


def log_llm_call(
    *,
    agent: str,
    model: str,
    tier: str,
    latency_seconds: float,
    input_tokens: int | None,
    output_tokens: int | None,
    status: str,
    error_message: str | None = None,
) -> None:
    record = {
        "timestamp": _now_iso(),
        "agent": agent,
        "model": model,
        "tier": tier,
        "latency_seconds": round(latency_seconds, 4),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "status": status,
        "error_message": error_message,
    }
    _jsonl_append(_LLM_LOG, record)
    _py_logger.info(
        "llm_call agent=%s model=%s tier=%s status=%s latency=%.3fs",
        agent,
        model,
        tier,
        status,
        latency_seconds,
    )


class AuditTrail:
    """Collects audit entries for a single /run and persists them to JSONL.

    Each entry captures: agent name, action, decision, reason, plus an optional
    loop tag and free-form details. This is the record judges/reviewers use to
    verify multi-agent collaboration and loop firings.
    """

    def __init__(self, run_id: str, mode: str) -> None:
        self.run_id = run_id
        self.mode = mode
        self.entries: list[dict[str, Any]] = []
        self._run_logger = logging.getLogger(f"wakeel.run.{run_id[:8]}")
        self._path = config.LOG_DIR / f"run_{run_id}.jsonl"

    def add(
        self,
        *,
        agent: str,
        action: str,
        decision: str = "",
        reason: str = "",
        loop: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": _now_iso(),
            "run_id": self.run_id,
            "mode": self.mode,
            "agent": agent,
            "action": action,
            "decision": decision,
            "reason": reason,
            "loop": loop,
            "details": details or {},
        }
        self.entries.append(entry)
        _jsonl_append(self._path, entry)
        self._run_logger.info(
            "audit agent=%s action=%s loop=%s decision=%s",
            agent,
            action,
            loop or "-",
            decision or "-",
        )
        return entry

    def as_list(self) -> list[dict[str, Any]]:
        return list(self.entries)
