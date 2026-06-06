"""Shared E2E test fixtures.

Two execution targets, chosen by environment:

  * In-process (default): a FastAPI ``TestClient`` against ``app.api.app``.
  * Live server: set ``WAKEEL_E2E_BASE_URL=http://localhost:8000`` to drive a
    running API instance (and, if that server has Compass/Ollama creds, real
    LLM calls).

Determinism: unless ``WAKEEL_E2E_LIVE=1`` is set, tests force ``OFFLINE_MODE``
so the deterministic stub engine makes Loops 1-3 fire reliably and assertions
are stable. Tests use an isolated Chroma dir so they never clobber dev data.
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# --- Decide mode BEFORE importing app (config reads env at import time) ---
LIVE = os.environ.get("WAKEEL_E2E_LIVE") == "1"
LIVE_BASE_URL = os.environ.get("WAKEEL_E2E_BASE_URL", "").strip()

if not LIVE:
    os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("SAMPLE_MODE", "true")
# Isolated vector store + logs for tests (don't pollute committed logs/).
os.environ.setdefault("CHROMA_DIR", str(_ROOT / ".pytest_chroma"))
os.environ.setdefault("LOG_DIR", str(_ROOT / ".pytest_logs"))

import pytest  # noqa: E402

from app import config  # noqa: E402

OFFLINE = config.OFFLINE_MODE


class _LiveClient:
    """Minimal requests-based client mirroring TestClient's surface."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def post(self, path: str, json: dict):
        import requests

        return requests.post(self.base_url + path, json=json, timeout=600)

    def get(self, path: str):
        import requests

        return requests.get(self.base_url + path, timeout=30)


@pytest.fixture(scope="session", autouse=True)
def _ensure_corpus():
    """Re-ingest once per session so embeddings match the current mode."""
    from app.corpus.ingest import ingest

    ingest(reset=True)
    yield


@pytest.fixture(scope="session")
def api():
    if LIVE_BASE_URL:
        return _LiveClient(LIVE_BASE_URL)
    from fastapi.testclient import TestClient

    from app.api import app

    return TestClient(app)


@pytest.fixture(scope="session")
def offline() -> bool:
    return OFFLINE
