"""Shared LLM client wrapper — the single gateway for ALL model access.

Implements every property required by SOW v2 section 6:

  1. Single shared OpenAI client, initialized once at module load from env vars.
  2. Model selection by tier ("standard" / "reasoning" / "embedding"), not name.
  3. Retry decorator: exponential backoff (tenacity), 4 attempts, 2s-30s.
  4. Sample mode: NO automatic token-cap injection. The wrapper does not send
     any `max_tokens` / `max_completion_tokens` to the SDK by default — quota
     management on Compass is enforced at the group level, not per request,
     and OpenAI-direct dev keys can opt in by passing the kwarg explicitly.
     The legacy `SAMPLE_MODE` env flag is retained for back-compat surfacing
     in `current_config()` but no longer alters wire behaviour. Any caller
     that still passes the legacy `max_tokens` kwarg is transparently
     translated to `max_completion_tokens` for GPT-5 / o-series compatibility.
  5. Mandatory chat(agent_name, tier, messages, **kwargs) signature.
  6. Mandatory embed(texts) signature.
  7. Structured JSONL logging on every call (timestamp, agent, model, tier,
     latency, input/output tokens, status, error_message).
  8. Agents only ever call chat()/embed(); they never import OpenAI.
  9. Env vars per .env.example.

OFFLINE behaviour (Wakeel addition): when OFFLINE_MODE is true (or no API key
is configured) the wrapper returns deterministic stub completions/embeddings so
the full graph, loops, UI, and audit logs run without credentials or quota.
The same logging contract is honoured (status="ok", model tagged "offline-stub").
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app import config
from app.logging_utils import log_llm_call

# --- Mutable runtime state -----------------------------------------------------
# Initialized from env at module load (Property 1/2/9), but reconfigurable at
# runtime so the API key / base URL / models can be changed from the front-end
# without restarting the process (provider flexibility requested by the client).
_state: dict[str, Any] = {
    "api_key": config.OPENAI_API_KEY,
    "base_url": config.OPENAI_BASE_URL,
    "offline": config.OFFLINE_MODE,
    "interviewer_model": config.INTERVIEWER_MODEL,
}

# --- Property 2: model selection by tier ---
MODELS: dict[str, str] = dict(config.MODELS)

# --- Property 1: single shared client (only constructed when online) ---
_client = None


def _build_client():
    """Construct the shared OpenAI client from current runtime state."""
    global _client
    if _state["offline"]:
        _client = None
        return None
    from openai import OpenAI

    _client = OpenAI(api_key=_state["api_key"], base_url=_state["base_url"] or None)
    return _client


_build_client()


def reconfigure(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    models: dict[str, str] | None = None,
    interviewer_model: str | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Update LLM configuration at runtime and rebuild the shared client.

    Called by the ``POST /config`` endpoint so the API key / endpoint / models
    can be switched from the UI. If ``offline`` is not given, it is inferred from
    whether an API key is present (mirrors the module-load behaviour).
    """
    if api_key is not None:
        _state["api_key"] = api_key.strip()
    if base_url is not None:
        _state["base_url"] = base_url.strip()
    if interviewer_model is not None:
        _state["interviewer_model"] = interviewer_model.strip()
    if models:
        for tier, name in models.items():
            if name:
                MODELS[tier] = name
    if offline is None:
        _state["offline"] = not _state["api_key"]
    else:
        _state["offline"] = bool(offline)
    _build_client()
    return current_config()


def current_config() -> dict[str, Any]:
    """Return the current config with the API key masked (safe to surface in UI)."""
    key = _state["api_key"] or ""
    masked = (key[:4] + "..." + key[-4:]) if len(key) > 8 else ("set" if key else "")
    return {
        "offline_mode": _state["offline"],
        "sample_mode": config.SAMPLE_MODE,
        "base_url": _state["base_url"],
        "api_key_masked": masked,
        "models": dict(MODELS),
        "interviewer_model": _state["interviewer_model"],
    }


def resolve_model(tier: str, *, agent_name: str | None = None) -> str:
    """Resolve a tier (and optionally agent) to a concrete model name.

    The Interviewer can use a dedicated Arabic-capable model (e.g. Jais) via
    INTERVIEWER_MODEL; if unset it falls back to the standard tier model. This
    keeps the Jais-vs-fallback decision a pure config change (SOW section 6).
    """
    if agent_name == "Interviewer" and _state["interviewer_model"]:
        return _state["interviewer_model"]
    if tier not in MODELS:
        raise ValueError(f"Unknown tier '{tier}'. Valid tiers: {list(MODELS)}")
    return MODELS[tier]


# --- Minimal response shapes (used in offline mode; mirror the OpenAI SDK) ---
@dataclass
class _Message:
    content: str
    role: str = "assistant"


@dataclass
class _Choice:
    message: _Message
    index: int = 0
    finish_reason: str = "stop"


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _ChatCompletion:
    choices: list[_Choice]
    usage: _Usage = field(default_factory=_Usage)
    model: str = "offline-stub"


# --- Property 3: retry decorator (4 attempts, exponential 2s-30s) ---
_retry = retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)


@_retry
def _online_chat(model: str, messages: list, **kwargs) -> Any:
    return _client.chat.completions.create(model=model, messages=messages, **kwargs)


@_retry
def _online_embed(model: str, texts: list[str]) -> Any:
    return _client.embeddings.create(model=model, input=texts)


def _estimate_tokens(messages: list) -> int:
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(1, chars // 4)


_RESPONSE_FORMAT_MARKERS = (
    "response_format",
    "json_object",
    "json mode",
    "json_schema",
    "unsupported parameter",
)


def _looks_like_response_format_rejection(exc: Exception) -> bool:
    """Detect ``BadRequestError`` from gateways that don't accept JSON mode.

    Compass GPT-5.1 honours ``response_format``; some open-weights deployments
    behind the same OpenAI-compatible surface do not. We retry once without it
    rather than fail the whole turn.
    """
    name = exc.__class__.__name__.lower()
    if "badrequest" not in name and "unsupported" not in name:
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _RESPONSE_FORMAT_MARKERS)


# --- Property 5: mandatory chat() ---
def chat(agent_name: str, tier: str, messages: list, **kwargs) -> Any:
    """All chat completions in the codebase go through this function.

    Agents must pass their name (required) for audit-trail attribution and
    request a tier, never a model name.
    """
    if not agent_name:
        raise ValueError("agent_name is required for every chat() call")

    model = resolve_model(tier, agent_name=agent_name)

    # The wrapper does NOT inject any token cap by default.
    #
    # Rationale: Compass enforces group-level quotas (no per-request
    # max_tokens enforcement), and GPT-5 / o-series reasoning models reject
    # the legacy `max_tokens` parameter outright. The SAMPLE_MODE feature
    # was a dev-only quota brake for OpenAI-direct keys; callers that still
    # want it must pass `max_completion_tokens` explicitly.
    #
    # Defensive translation: any caller that still hands us the legacy
    # `max_tokens` (per the original SOW §6 wording) is silently routed to
    # `max_completion_tokens` so GPT-5.1 on Compass doesn't 400 on us.
    if "max_tokens" in kwargs and "max_completion_tokens" not in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

    # JSON mode: opt-in by callers that parse structured output. GPT-5.1 on
    # Compass adheres to schemas far more reliably when response_format is
    # set; some gateways/older deployments reject it, in which case we
    # transparently retry once without it.
    json_mode = bool(kwargs.pop("json_mode", False))
    if json_mode:
        kwargs.setdefault("response_format", {"type": "json_object"})

    offline = _state["offline"]
    start = time.perf_counter()
    status = "ok"
    error_message: str | None = None
    completion: Any = None
    try:
        if offline:
            from app.offline_stubs import stub_chat

            content = stub_chat(agent_name, messages)
            prompt_tokens = _estimate_tokens(messages)
            completion_tokens = max(1, len(content) // 4)
            completion = _ChatCompletion(
                choices=[_Choice(message=_Message(content=content))],
                usage=_Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                model=f"offline-stub:{model}",
            )
        else:
            try:
                completion = _online_chat(model, messages, **kwargs)
            except Exception as exc:
                if json_mode and _looks_like_response_format_rejection(exc):
                    kwargs.pop("response_format", None)
                    completion = _online_chat(model, messages, **kwargs)
                else:
                    raise
        return completion
    except Exception as exc:  # noqa: BLE001 - logged then re-raised
        status = "error"
        error_message = f"{exc.__class__.__name__}: {exc}"
        raise
    finally:
        latency = time.perf_counter() - start
        usage = getattr(completion, "usage", None)
        log_llm_call(
            agent=agent_name,
            model=f"offline-stub:{model}" if offline else model,
            tier=tier,
            latency_seconds=latency,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            status=status,
            error_message=error_message,
        )


# --- Property 6: mandatory embed() ---
def embed(texts: list[str]) -> list[list[float]]:
    """All embedding calls in the codebase go through this function."""
    model = resolve_model("embedding")
    offline = _state["offline"]
    start = time.perf_counter()
    status = "ok"
    error_message: str | None = None
    vectors: list[list[float]] = []
    try:
        if offline:
            from app.offline_stubs import stub_embed

            vectors = [stub_embed(t) for t in texts]
        else:
            resp = _online_embed(model, texts)
            vectors = [d.embedding for d in resp.data]
        return vectors
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_message = f"{exc.__class__.__name__}: {exc}"
        raise
    finally:
        latency = time.perf_counter() - start
        log_llm_call(
            agent="embed",
            model=f"offline-stub:{model}" if offline else model,
            tier="embedding",
            latency_seconds=latency,
            input_tokens=sum(len(t) // 4 for t in texts) or None,
            output_tokens=None,
            status=status,
            error_message=error_message,
        )


def message_content(completion: Any) -> str:
    """Convenience accessor used by agents to read the assistant text."""
    return completion.choices[0].message.content or ""


def _smoke_test() -> int:
    """Smoke test (Amendment M1 criterion #1): `python -m app.llm`.

    Sends one request through the wrapper against the configured endpoint and
    prints a valid response. Returns 0 on success, 1 on failure.
    """
    cfg = current_config()
    print("Wakeel LLM smoke test")
    print(f"  mode      : {'OFFLINE (stub)' if cfg['offline_mode'] else 'LIVE'}")
    print(f"  base_url  : {cfg['base_url'] or '(default)'}")
    print(f"  api_key   : {cfg['api_key_masked'] or '(none)'}")
    print(f"  models    : {cfg['models']}")
    try:
        completion = chat(
            "Interviewer",
            "standard",
            [{"role": "user", "content": "Reply with a short confirmation that you are reachable."}],
        )
        text = message_content(completion)
        print(f"  response  : {text[:200]!r}")
        usage = getattr(completion, "usage", None)
        print(
            f"  tokens    : in={getattr(usage, 'prompt_tokens', '?')} "
            f"out={getattr(usage, 'completion_tokens', '?')}"
        )
        assert text and text.strip(), "empty response"
        print("RESULT: OK")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"RESULT: FAILED — {exc.__class__.__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
