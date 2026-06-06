"""Read-only decision history per copilot.

Wraps the existing per-run audit trail files (``logs/run_<run_id>.jsonl``,
written by ``app.logging_utils.AuditTrail``) and groups them by ``copilot_id``.

This is the read-side of the audit trail. The write-side is unchanged — every
agent action is still appended to its run's JSONL by ``AuditTrail.add``. We
simply scan, parse, and filter on demand. No database, no schema, no new
dependencies.

How ``copilot_id`` is recovered from a log file:
  * **Build mode**: the Builder agent records ``details.copilot_id`` on the
    ``build_copilot`` action (see ``app.agents.build_agents.builder``).
  * **Use mode**: the orchestrator's ``run_start`` entry records
    ``details.copilot_id`` (see ``app.graph.use_graph.run_use``).

A run "belongs to" a copilot iff at least one entry has a matching
``details.copilot_id`` (or, as a defensive fallback for older use-mode logs
written before the run_start tagging was added, a substring match on the
``reason`` field of the orchestrator entry).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import config


_RUN_FILENAME_RE = re.compile(r"^run_[0-9a-f-]+\.jsonl$")
_REASON_COPILOT_RE = re.compile(r"copilot=(\S+)")


def _entry_copilot_ids(entry: dict[str, Any]) -> set[str]:
    """Return every copilot_id that this single audit entry references."""
    ids: set[str] = set()
    details = entry.get("details") or {}
    if isinstance(details, dict):
        cid = details.get("copilot_id")
        if isinstance(cid, str) and cid:
            ids.add(cid)
    reason = entry.get("reason") or ""
    if isinstance(reason, str):
        for m in _REASON_COPILOT_RE.finditer(reason):
            ids.add(m.group(1))
    return ids


def _read_run_log(path: Path) -> list[dict[str, Any]]:
    """Parse a single run JSONL, skipping malformed lines defensively."""
    entries: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # Resilience: skip malformed lines; do not 500 the endpoint.
                    continue
    except OSError:
        return []
    return entries


def _summarize_run(run_id: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the per-run summary the /decisions endpoint returns."""
    mode = next(
        (e.get("mode") for e in entries if isinstance(e.get("mode"), str) and e.get("mode")),
        "",
    )
    timestamps = [e.get("timestamp") for e in entries if isinstance(e.get("timestamp"), str)]
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None
    loops_fired = sorted(
        {e["loop"] for e in entries if isinstance(e.get("loop"), str) and e["loop"]}
    )
    return {
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "ended_at": ended_at,
        "entry_count": len(entries),
        "loops_fired": loops_fired,
        "entries": entries,
    }


def _list_run_files(log_dir: Path) -> list[Path]:
    if not log_dir.exists():
        return []
    return sorted(
        p for p in log_dir.glob("run_*.jsonl") if _RUN_FILENAME_RE.match(p.name)
    )


def history_for_copilot(
    copilot_id: str,
    *,
    log_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return every run whose audit trail references ``copilot_id``.

    Runs are returned sorted by ``started_at`` ascending (oldest first), which
    matches the order they were executed. Each run is a dict shaped as
    ``_summarize_run`` describes.

    Pure read operation. No mutation of state, no LLM calls, no network.
    """
    log_dir = log_dir or config.LOG_DIR
    if not copilot_id:
        return []
    out: list[dict[str, Any]] = []
    for path in _list_run_files(log_dir):
        entries = _read_run_log(path)
        if not entries:
            continue
        matched = any(copilot_id in _entry_copilot_ids(e) for e in entries)
        if not matched:
            continue
        run_id = path.stem[len("run_"):]
        out.append(_summarize_run(run_id, entries))
    out.sort(key=lambda r: r.get("started_at") or "")
    return out
