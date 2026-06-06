"""Wrapper-level tests for `app/llm.py` — SOW v2 section 6 properties.

These tests verify the LLM wrapper directly (without a live network call) so
SOW §6 invariants hold across model swaps, agent attribution, and per-tier
routing. Where useful, an in-memory fake of the OpenAI SDK shape captures the
exact kwargs sent to `client.chat.completions.create()` and asserts what the
wrapper composed.

SOW §6 properties exercised here:

  2. Tier-based model resolution (standard / reasoning / embedding) via env.
  4. SAMPLE_MODE response-length cap (now `max_completion_tokens`).
  5. `chat(agent_name, tier, messages, **kwargs)` — required agent_name,
     kwargs pass-through to the SDK, tier swap → different model string.
  6. `embed(texts)` — embedding-tier model, JSONL log emitted.
  9. Env vars (DEFAULT_MODEL / REASONING_MODEL / EMBEDDING_MODEL /
     INTERVIEWER_MODEL) drive resolution, swappable at runtime via
     `reconfigure()`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


# ----------------------------------------------------------------------------
# Shared fixture: a fake OpenAI client that captures every `create()` call.
# ----------------------------------------------------------------------------

class _FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _FakeChoice:
    def __init__(self, content: str = "ok") -> None:
        self.message = type("M", (), {"content": content})()
        self.index = 0
        self.finish_reason = "stop"


class _FakeCompletion:
    def __init__(self, model: str = "fake-model") -> None:
        self.choices = [_FakeChoice()]
        self.usage = _FakeUsage()
        self.model = model


class _FakeEmbeddingItem:
    def __init__(self, vec: list[float]) -> None:
        self.embedding = vec


class _FakeEmbeddingResponse:
    def __init__(self, n: int, dim: int = 4) -> None:
        self.data = [_FakeEmbeddingItem([0.0] * dim) for _ in range(n)]


class _FakeChatCompletions:
    def __init__(self, store: dict) -> None:
        self.store = store

    def create(self, **kwargs):
        self.store["last_chat"] = kwargs
        self.store.setdefault("chat_calls", []).append(kwargs)
        return _FakeCompletion(model=kwargs.get("model", "fake-model"))


class _FakeEmbeddings:
    def __init__(self, store: dict) -> None:
        self.store = store

    def create(self, **kwargs):
        self.store["last_embed"] = kwargs
        return _FakeEmbeddingResponse(n=len(kwargs.get("input", [])))


class _FakeClient:
    def __init__(self) -> None:
        self.store: dict = {}
        self.chat = type("Chat", (), {"completions": _FakeChatCompletions(self.store)})()
        self.embeddings = _FakeEmbeddings(self.store)


@pytest.fixture
def online_llm(monkeypatch):
    """Patch the wrapper to use a fake client and force `online` mode.

    Yields the `(llm_module, fake_client)` pair so tests can both call the
    public API and inspect what hit the fake SDK.
    """
    from app import llm

    fake = _FakeClient()
    monkeypatch.setattr(llm, "_client", fake, raising=False)
    monkeypatch.setitem(llm._state, "offline", False)
    return llm, fake


# ============================================================================
# A. Tier-based model resolution (SOW §6 property 2)
# ============================================================================

def test_resolve_standard_tier_returns_default_model(monkeypatch):
    from app import llm

    monkeypatch.setitem(llm.MODELS, "standard", "gpt-4.1-test")
    assert llm.resolve_model("standard") == "gpt-4.1-test"


def test_resolve_reasoning_tier_returns_reasoning_model(monkeypatch):
    from app import llm

    monkeypatch.setitem(llm.MODELS, "reasoning", "gpt-5.1-test")
    assert llm.resolve_model("reasoning") == "gpt-5.1-test"


def test_resolve_embedding_tier_returns_embedding_model(monkeypatch):
    from app import llm

    monkeypatch.setitem(llm.MODELS, "embedding", "text-embedding-3-large-test")
    assert llm.resolve_model("embedding") == "text-embedding-3-large-test"


def test_resolve_unknown_tier_raises_valueerror():
    from app import llm

    with pytest.raises(ValueError, match="Unknown tier"):
        llm.resolve_model("creative")


# ============================================================================
# B. Per-agent override — INTERVIEWER_MODEL (SOW §6 + Jais hint)
# ============================================================================

def test_interviewer_agent_uses_interviewer_model_when_set(monkeypatch):
    """When INTERVIEWER_MODEL is configured (e.g. Jais for sovereign Arabic),
    the Interviewer agent uses it regardless of requested tier.
    """
    from app import llm

    monkeypatch.setitem(llm._state, "interviewer_model", "jais-30b-msa")
    monkeypatch.setitem(llm.MODELS, "standard", "gpt-4.1")
    assert llm.resolve_model("standard", agent_name="Interviewer") == "jais-30b-msa"


def test_interviewer_agent_falls_back_to_tier_when_override_empty(monkeypatch):
    from app import llm

    monkeypatch.setitem(llm._state, "interviewer_model", "")
    monkeypatch.setitem(llm.MODELS, "standard", "gpt-4.1")
    assert llm.resolve_model("standard", agent_name="Interviewer") == "gpt-4.1"


def test_non_interviewer_agent_ignores_interviewer_override(monkeypatch):
    """The Interviewer override is *per-agent only* — Debater, Reviewer, etc.
    still resolve to their requested tier."""
    from app import llm

    monkeypatch.setitem(llm._state, "interviewer_model", "jais-30b-msa")
    monkeypatch.setitem(llm.MODELS, "reasoning", "gpt-5.1")
    assert llm.resolve_model("reasoning", agent_name="Debater A") == "gpt-5.1"
    assert llm.resolve_model("reasoning", agent_name="Reviewer") == "gpt-5.1"


# ============================================================================
# C. chat() — required agent_name + kwargs passthrough (SOW §6 property 5)
# ============================================================================

def test_chat_requires_agent_name(online_llm):
    llm, _ = online_llm
    with pytest.raises(ValueError, match="agent_name is required"):
        llm.chat("", "standard", [{"role": "user", "content": "x"}])
    with pytest.raises(ValueError, match="agent_name is required"):
        llm.chat(None, "standard", [{"role": "user", "content": "x"}])  # type: ignore[arg-type]


def test_chat_sends_resolved_model_per_tier(online_llm, monkeypatch):
    """Two calls on different tiers must hit the SDK with different `model`
    strings — that's the whole point of tier abstraction (SOW §6 property 2)."""
    llm, fake = online_llm
    monkeypatch.setitem(llm.MODELS, "standard", "gpt-4.1-test")
    monkeypatch.setitem(llm.MODELS, "reasoning", "gpt-5.1-test")
    monkeypatch.setitem(llm._state, "interviewer_model", "")

    llm.chat("Reviewer", "standard", [{"role": "user", "content": "a"}])
    standard_kwargs = dict(fake.store["last_chat"])
    llm.chat("Reviewer", "reasoning", [{"role": "user", "content": "b"}])
    reasoning_kwargs = dict(fake.store["last_chat"])

    assert standard_kwargs["model"] == "gpt-4.1-test"
    assert reasoning_kwargs["model"] == "gpt-5.1-test"
    assert standard_kwargs["model"] != reasoning_kwargs["model"]


def test_chat_kwargs_passthrough_temperature_top_p(online_llm):
    """Caller-supplied generation parameters flow through to the SDK call."""
    llm, fake = online_llm
    llm.chat(
        "Reviewer",
        "standard",
        [{"role": "user", "content": "x"}],
        temperature=0.2,
        top_p=0.9,
        presence_penalty=0.5,
    )
    sent = fake.store["last_chat"]
    assert sent["temperature"] == 0.2
    assert sent["top_p"] == 0.9
    assert sent["presence_penalty"] == 0.5


def test_chat_does_not_inject_token_cap_by_default(online_llm):
    """The wrapper must not inject any token cap of its own. Compass enforces
    group-level quotas, not per-request limits — sending a cap unnecessarily
    is what broke M1 verification against live Compass GPT-5.1."""
    llm, fake = online_llm
    llm.chat("Reviewer", "standard", [{"role": "user", "content": "x"}])
    sent = fake.store["last_chat"]
    assert "max_tokens" not in sent, sent
    assert "max_completion_tokens" not in sent, sent


def test_chat_passes_through_caller_max_completion_tokens(online_llm):
    """Caller-supplied max_completion_tokens flows through unchanged — opt-in
    quota discipline for developers using their own OpenAI key."""
    llm, fake = online_llm
    llm.chat(
        "Reviewer",
        "standard",
        [{"role": "user", "content": "x"}],
        max_completion_tokens=42,
    )
    assert fake.store["last_chat"]["max_completion_tokens"] == 42


def test_chat_translates_legacy_max_tokens_to_max_completion_tokens(online_llm):
    """Defensive: any caller that hands us the legacy `max_tokens` kwarg (per
    the original SOW §6 wording) is silently translated so the call still
    works against GPT-5 / o-series models."""
    llm, fake = online_llm
    llm.chat(
        "Reviewer",
        "reasoning",
        [{"role": "user", "content": "x"}],
        max_tokens=77,
    )
    sent = fake.store["last_chat"]
    assert "max_tokens" not in sent, "legacy max_tokens must not leak through"
    assert sent["max_completion_tokens"] == 77


def test_sample_mode_true_no_longer_injects_anything(online_llm, monkeypatch):
    """Historical SAMPLE_MODE=true injection has been removed per PO
    clarification — even when the flag is on, the wire call must be clean."""
    llm, fake = online_llm
    monkeypatch.setattr(llm.config, "SAMPLE_MODE", True)
    llm.chat("Reviewer", "standard", [{"role": "user", "content": "x"}])
    sent = fake.store["last_chat"]
    assert "max_tokens" not in sent
    assert "max_completion_tokens" not in sent


def test_chat_uses_interviewer_model_end_to_end(online_llm, monkeypatch):
    """Combined: Interviewer agent → SDK call uses the configured Jais-like model."""
    llm, fake = online_llm
    monkeypatch.setitem(llm._state, "interviewer_model", "G42-INCEPTION-GPT41-MSA")
    monkeypatch.setitem(llm.MODELS, "standard", "gpt-4.1")
    llm.chat("Interviewer", "standard", [{"role": "user", "content": "مرحبا"}])
    assert fake.store["last_chat"]["model"] == "G42-INCEPTION-GPT41-MSA"


# ============================================================================
# D. embed() — SOW §6 property 6 + tier=embedding routing
# ============================================================================

def test_embed_uses_embedding_tier_model(online_llm, monkeypatch):
    llm, fake = online_llm
    monkeypatch.setitem(llm.MODELS, "embedding", "text-embedding-3-large-test")
    vectors = llm.embed(["alpha", "beta"])
    assert fake.store["last_embed"]["model"] == "text-embedding-3-large-test"
    assert isinstance(vectors, list) and len(vectors) == 2
    assert all(isinstance(v, list) and all(isinstance(x, float) for x in v) for v in vectors)


def test_embed_offline_returns_deterministic_vectors():
    """Offline path: deterministic stub vectors with a stable shape — the
    full graph runs without credentials per the wrapper's offline contract."""
    from app import llm

    a = llm.embed(["alpha", "beta"])
    b = llm.embed(["alpha", "beta"])
    assert a == b, "offline embedding must be deterministic"
    assert all(isinstance(v, list) for v in a)


# ============================================================================
# E. reconfigure() — runtime model + endpoint swap (provider flexibility)
# ============================================================================

def test_reconfigure_swaps_models_at_runtime(monkeypatch):
    """`reconfigure()` lets the front-end change models / keys / endpoint
    without restarting — the same tier now resolves to a new model."""
    from app import llm

    # Snapshot anything reconfigure() mutates so subsequent tests aren't
    # tainted. reconfigure() recomputes _state["offline"] from api_key, which
    # made earlier runs spill offline=False into test_use_mode_robustness.
    saved_offline = llm._state["offline"]
    saved_models = dict(llm.MODELS)
    monkeypatch.setitem(llm.MODELS, "standard", "old-standard")
    llm.reconfigure(models={"standard": "new-standard"})
    try:
        assert llm.resolve_model("standard") == "new-standard"
    finally:
        llm.MODELS.clear()
        llm.MODELS.update(saved_models)
        llm._state["offline"] = saved_offline
        llm._build_client()


def test_current_config_masks_api_key(monkeypatch):
    """The current-config endpoint must never surface the raw API key."""
    from app import llm

    monkeypatch.setitem(llm._state, "api_key", "sk-abcdefghijklmnop")
    cfg = llm.current_config()
    masked = cfg["api_key_masked"]
    assert "sk-abcdefghijklmnop" not in str(cfg)
    assert masked.startswith("sk-a")
    assert masked.endswith("mnop")
    assert "..." in masked


# ============================================================================
# F. Per-call JSONL logging (SOW §6 property 7) — works across tiers
# ============================================================================

def test_chat_logs_every_call_with_required_fields(online_llm):
    """Every chat() call appends a JSONL record with all 8 SOW-required fields.

    We snapshot the canonical log path (set by conftest.py) before/after a
    distinctive call and assert the new tail entry matches.
    """
    from app import logging_utils, llm

    log_file = logging_utils._LLM_LOG
    pre_lines = log_file.read_text().splitlines() if log_file.exists() else []
    pre_count = len(pre_lines)

    llm_mod, _ = online_llm
    llm_mod.chat(
        "WrapperTestAgent",
        "reasoning",
        [{"role": "user", "content": "regression-marker"}],
    )

    post_lines = log_file.read_text().splitlines()
    assert len(post_lines) > pre_count, "no new log line appended"

    last = json.loads(post_lines[-1])
    required = {
        "timestamp", "agent", "model", "tier",
        "latency_seconds", "input_tokens", "output_tokens", "status",
    }
    assert required <= set(last), set(last)
    assert last["agent"] == "WrapperTestAgent"
    assert last["tier"] == "reasoning"
    assert last["status"] == "ok"
    assert last["latency_seconds"] >= 0


def test_chat_logs_status_error_on_sdk_failure(online_llm, monkeypatch):
    """If the underlying SDK raises, the wrapper still emits a log record
    with status='error' (so failures are auditable).

    We bypass the tenacity retry layer (which otherwise waits exponentially)
    by replacing `_online_chat` itself — the goal is to verify the error
    logging contract, not the retry policy.
    """
    from app import logging_utils, llm

    llm_mod, _ = online_llm

    def _raises(model, messages, **kwargs):
        raise RuntimeError("simulated rate-limit / 5xx")

    monkeypatch.setattr(llm_mod, "_online_chat", _raises)

    log_file = logging_utils._LLM_LOG
    pre = log_file.read_text().splitlines() if log_file.exists() else []

    with pytest.raises(RuntimeError):
        llm_mod.chat("Reviewer", "reasoning", [{"role": "user", "content": "x"}])

    post = log_file.read_text().splitlines()
    assert len(post) > len(pre), "error path must still produce a log record"
    last = json.loads(post[-1])
    assert last["status"] == "error"
    assert "RuntimeError" in (last.get("error_message") or "")
