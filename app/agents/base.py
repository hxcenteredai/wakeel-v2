"""Shared agent helpers.

Agents NEVER import OpenAI or instantiate a client. They call ``call_llm`` which
delegates to the shared wrapper (app.llm.chat) and parses structured output.
A compact JSON ``context`` block is appended to the prompt behind the ``@@CTX@@``
marker so the offline stub engine can produce schema-valid responses; live models
simply receive it as additional structured context.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.llm import chat, message_content
from app.offline_stubs import CTX_MARKER

# Top-level keys models sometimes use to wrap a payload instead of returning the
# requested keys directly (e.g. ``{"result": {"findings": [...]}}``). Order
# matters: most-specific aliases first.
_WRAPPER_KEYS = ("result", "data", "output", "response", "analysis", "report", "payload")


def _coerce_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON OBJECT from a model response.

    ALWAYS returns a dict. Real models sometimes emit a bare/quoted JSON value
    (e.g. ``"ok"`` parses to a str) or prose; in those cases we wrap the content
    as ``{"_raw": ...}`` so downstream agent code can always call ``.get()``
    without crashing.
    """
    text = (text or "").strip()
    # Strip code fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} block.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"_raw": text}


def _unwrap_envelope(parsed: dict[str, Any], expect_keys: list[str] | None) -> dict[str, Any]:
    """Live models sometimes wrap the payload in a generic envelope.

    If none of ``expect_keys`` are at the top level but a single nested dict
    under a common wrapper key contains them, hoist that nested dict up.
    Pure passthrough when no envelope is detected.
    """
    if not expect_keys or any(k in parsed for k in expect_keys):
        return parsed
    for wrapper in _WRAPPER_KEYS:
        inner = parsed.get(wrapper)
        if isinstance(inner, dict) and any(k in inner for k in expect_keys):
            return inner
    return parsed


def _looks_incomplete(parsed: dict[str, Any], expect_keys: list[str] | None) -> bool:
    """True if the parsed object is unusable (bare _raw or missing all keys)."""
    if "_raw" in parsed and len(parsed) == 1:
        return True
    if expect_keys and not any(k in parsed for k in expect_keys):
        return True
    return False


# --- Type-coercion helpers ----------------------------------------------------
# Live models occasionally return primitives where dicts or lists are expected
# (e.g. ``{"citation": 43}`` instead of ``{"citation": {"law": ..., "article":
# 43}}``). Direct subscripting then crashes deep in the graph. These helpers
# give every consumer a uniform "treat anything non-dict-like as the safe
# default" guarantee without hiding the underlying value entirely.


def as_dict(value: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``value`` if it's a dict; otherwise the default (a fresh empty
    dict by default). Scalars are preserved under the ``_raw`` key so debugging
    isn't lossy.
    """
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return dict(default or {})
    out = dict(default or {})
    out.setdefault("_raw", value)
    return out


def as_list(value: Any, default: list | None = None) -> list:
    """Return ``value`` if it's a list; otherwise the default (empty list).

    Non-list iterables and scalars are NOT auto-converted — a model returning
    ``"three issues"`` for a list field is more likely a parsing failure than a
    one-element list, so we surface the safe default and let the prompt-level
    retry catch it.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # Some models wrap a list under a single key (e.g. ``{"items": [...]}``)
        # — accept that one-key shape, otherwise fall back to default.
        if len(value) == 1:
            only = next(iter(value.values()))
            if isinstance(only, list):
                return only
    return list(default or [])


def call_llm(
    *,
    agent_name: str,
    tier: str,
    system: str,
    user: str,
    ctx: dict[str, Any] | None = None,
    parse_json: bool = True,
    expect_keys: list[str] | None = None,
    **kwargs,
) -> tuple[Any, str]:
    """Call the shared LLM wrapper and parse structured output.

    When ``parse_json`` is set, the result is ALWAYS a dict. If the model returns
    unusable output (non-JSON, or missing all ``expect_keys``), we retry once with
    a stricter instruction before giving up — defends the graph against ragged
    output from weaker models without changing agent code.

    When ``parse_json`` is set, we also opt into the wrapper's JSON mode
    (``response_format={"type":"json_object"}``) so GPT-5.1-class models stop
    wrapping the payload in prose or code fences. The wrapper transparently
    falls back if the gateway rejects it.

    Returns (parsed_or_raw, raw_text).
    """
    user_content = user
    if ctx is not None:
        user_content = f"{user}\n\n{CTX_MARKER} {json.dumps(ctx, ensure_ascii=False)}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    # Opt into structured-output mode whenever we're going to parse JSON. The
    # offline stub ignores it; live Compass GPT-5.1 honours it strictly.
    if parse_json:
        kwargs.setdefault("json_mode", True)

    completion = chat(agent_name, tier, messages, **kwargs)
    raw = message_content(completion)
    if not parse_json:
        return raw, raw

    parsed = _unwrap_envelope(_coerce_json(raw), expect_keys)
    if _looks_incomplete(parsed, expect_keys):
        # One corrective retry: re-ask for a single strict JSON object.
        nudge = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your previous reply was not a single valid JSON object. "
                    "Respond again with ONLY a JSON object"
                    + (f" containing keys {expect_keys}" if expect_keys else "")
                    + ". No prose, no code fences."
                ),
            },
        ]
        retry = chat(agent_name, tier, nudge, **kwargs)
        retry_raw = message_content(retry)
        retry_parsed = _unwrap_envelope(_coerce_json(retry_raw), expect_keys)
        if not _looks_incomplete(retry_parsed, expect_keys):
            return retry_parsed, retry_raw
    return parsed, raw
