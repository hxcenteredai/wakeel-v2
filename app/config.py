"""Centralized environment/config loading.

Single place that reads environment variables so the rest of the codebase
never touches os.environ directly for configuration.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime (e.g. in Docker with env vars)
    pass


# Quiet ChromaDB's anonymized telemetry (keeps logs clean for the audit trail).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- LLM endpoint ---
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "").strip()

# --- Model tiers ---
MODELS: dict[str, str] = {
    "standard": os.environ.get("DEFAULT_MODEL", "gpt-4.1"),
    "reasoning": os.environ.get("REASONING_MODEL", "gpt-5.1"),
    "embedding": os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large"),
}

# Optional dedicated Arabic model (e.g. Jais) for the Interviewer.
INTERVIEWER_MODEL: str = os.environ.get("INTERVIEWER_MODEL", "").strip()

# --- Behaviour flags ---
SAMPLE_MODE: bool = _as_bool(os.environ.get("SAMPLE_MODE"), default=False)
SAMPLE_MODE_MAX_TOKENS: int = int(os.environ.get("SAMPLE_MODE_MAX_TOKENS", "300"))

# Offline mode is forced on when no API key is configured, so the system is
# always runnable for development/demo without credentials or quota usage.
OFFLINE_MODE: bool = _as_bool(os.environ.get("OFFLINE_MODE")) or not OPENAI_API_KEY

# --- Paths ---
_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR: Path = Path(os.environ.get("WAKEEL_DATA_DIR", _ROOT / "data"))
LOG_DIR: Path = Path(os.environ.get("LOG_DIR", _ROOT / "logs"))
CHROMA_DIR: Path = Path(os.environ.get("CHROMA_DIR", DATA_DIR / "chroma"))
CORPUS_CONFIG: Path = Path(
    os.environ.get("CORPUS_CONFIG", DATA_DIR / "corpus" / "corpus_config.json")
)

# --- Service wiring ---
WAKEEL_BACKEND_URL: str = os.environ.get("WAKEEL_BACKEND_URL", "http://localhost:8000")

LOG_DIR.mkdir(parents=True, exist_ok=True)
