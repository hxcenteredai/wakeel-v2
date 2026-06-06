"""File-backed copilot registry.

Build mode mints a `copilot_id` and writes its config to disk. Use mode reads
the config back. Simple JSON-per-copilot layout under `data/copilots/`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import config

_DIR = config.DATA_DIR / "copilots"


def _ensure_dir() -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR


def save(copilot_id: str, copilot_config: dict[str, Any]) -> Path:
    _ensure_dir()
    path = _DIR / f"{copilot_id}.json"
    payload = dict(copilot_config)
    payload["copilot_id"] = copilot_id
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load(copilot_id: str) -> dict[str, Any] | None:
    path = _DIR / f"{copilot_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_copilots() -> list[dict[str, Any]]:
    if not _DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "copilot_id": cfg.get("copilot_id", path.stem),
                "template": cfg.get("template"),
                "org_id": cfg.get("org_id"),
            }
        )
    return out
