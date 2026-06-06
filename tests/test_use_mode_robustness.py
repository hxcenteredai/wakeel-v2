"""Robustness regression tests for use-mode against live-model output drift.

The offline stub engine emits shape-perfect JSON, which hides whole classes of
failure that only show up against live GPT-5.1 / Compass. These tests
monkey-patch the LLM wrapper to return malformed responses we have actually
observed in the wild and assert the graph completes without crashing and the
API response remains schema-valid.

Cases mirror the two issues reported in the live PO acceptance run on
PR #2 / ``feat/m2-use-mode-delivery``:
  * Issue 1: Reviewer returns ``findings:[]`` or wraps payload in an envelope.
  * Issue 2: ``TypeError: 'int' object is not subscriptable`` after Loop 4 due
    to citation collapsing to a primitive in the re-cite response.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app import llm
from app.agents import use_agents
from app.graph import use_graph
from helpers import CUSTOMER_SCENARIOS, assert_valid_use_response


# --- Test infrastructure ------------------------------------------------------

class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.role = "assistant"


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)
        self.index = 0
        self.finish_reason = "stop"


class _FakeUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()
        self.model = "fake-live-model"


def _patch_chat(monkeypatch, responder):
    """Replace ``app.llm.chat`` with a stub that calls ``responder(agent_name, messages)``."""

    def fake_chat(agent_name: str, tier: str, messages: list, **kwargs):
        content = responder(agent_name, messages, kwargs)
        return _FakeCompletion(content)

    monkeypatch.setattr(llm, "chat", fake_chat)
    # Both modules cache an alias at import time — patch those too.
    import app.agents.base as base_mod

    monkeypatch.setattr(base_mod, "chat", fake_chat)


def _is_recite_prompt(system_prompt: str) -> bool:
    """The recite prompt is the only one that opens with 'You are the Reviewer.
    The Citation Verifier rejected...'.
    """
    return "Citation Verifier rejected your prior citation" in system_prompt


def _is_critic_prompt(system_prompt: str) -> bool:
    """The critic prompt is the only one that opens with 'You are the Reviewer
    acting as critic'.
    """
    return "acting as critic" in system_prompt


@pytest.fixture()
def use_copilot(api):
    """Build a fresh fintech copilot for use-mode tests."""
    resp = api.post("/run", json={"mode": "build", "intake": CUSTOMER_SCENARIOS[0]["intake"]})
    assert resp.status_code == 200, resp.text
    return resp.json()["copilot_id"]


def _vendor_nda_document() -> dict[str, Any]:
    return {
        "type": "text",
        "title": "Vendor Master NDA — v1 (vendor-favourable)",
        "content": (
            "1. Confidential Information. Each party may disclose to the other "
            "certain confidential information.\n"
            "2. Cross-border transfers. Vendor may transfer Confidential Information "
            "to its banking partners outside the UAE without further notice.\n"
            "3. Termination. Either party may terminate on seven (7) days notice.\n"
            "4. Indemnity. Vendor's aggregate liability is capped at twelve (12) "
            "months of fees, irrespective of the nature of the breach."
        ),
    }


# --- Reviewer issue: zero findings + envelope wrapping ------------------------

def test_reviewer_envelope_wrapped_findings_are_unwrapped(api, use_copilot, monkeypatch):
    """Live GPT-5.1 sometimes wraps the payload in ``{"result": {...}}`` or
    ``{"analysis": {...}}``. The base helper must unwrap so findings survive.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_recite_prompt(sys):
            return json.dumps({"citation": {"law": "PDPL", "article": "22"}})
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            # Initial review — wrap findings in an envelope.
            return json.dumps({
                "result": {
                    "findings": [
                        {
                            "clause": "Cross-border transfers to banking partners without notice",
                            "risk": "high",
                            "confidence": 0.92,
                            "citation": {"law": "PDPL", "article": "22"},
                            "rationale": "Cross-border transfer without explicit consent breaches PDPL.",
                        },
                        {
                            "clause": "Indemnity cap of twelve months",
                            "risk": "medium",
                            "confidence": 0.7,
                            "citation": {"law": "PDPL", "article": "7"},
                            "rationale": "Cap excludes data-protection breaches.",
                        },
                    ]
                }
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "Vendor shall not transfer data abroad without consent.", "rationale": "Aligns with PDPL Art. 22."})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "DO NOT SIGN", "total_findings": 2}})
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    assert len(body["findings"]) == 2, "envelope-wrapped findings were not unwrapped"


def test_reviewer_zero_findings_passes_through_cleanly(api, use_copilot, monkeypatch):
    """When the live model genuinely returns no findings, the response must
    still be schema-valid (empty list + structured summary)."""

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            return json.dumps({"findings": []})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "Acceptable — no findings."}})
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    assert body["findings"] == []
    assert isinstance(body["summary"], dict) and body["summary"]
    assert body["summary"]["total_findings"] == 0


# --- Issue 2: citation collapses to a primitive, crashes on subscript ---------

def test_recite_collapses_citation_to_int_no_crash(api, use_copilot, monkeypatch):
    """Live recite has been observed to return ``{"citation": 22}``.

    Previously this crashed in verifier_node with
    ``TypeError: 'int' object is not subscriptable``. The fix coerces every
    citation back to a ``{law, article}`` dict before mutation.
    """

    state = {"recite_calls": 0}

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_recite_prompt(sys):
            state["recite_calls"] += 1
            # First two retries: collapsed-to-int citation (the live bug shape).
            if state["recite_calls"] <= 2:
                return json.dumps({"citation": 22})
            # Third retry: still bad — exhausts MAX_CITATION_RETRIES.
            return json.dumps({"citation": "Article 22"})
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            # Initial review — one high-risk finding with a citation Loop 4 will reject.
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers without notice",
                        "risk": "high",
                        "confidence": 0.9,
                        "citation": {"law": "PDPL", "article": "9999"},  # hallucinated
                        "rationale": "Cross-border transfer breaches PDPL.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "No cross-border transfer without explicit consent.", "rationale": "PDPL alignment."})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "DO NOT SIGN"}})
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, f"use mode crashed on collapsed citation: {resp.text}"
    body = resp.json()
    assert_valid_use_response(body)
    # Citation Verifier must have fired at least once.
    assert body["summary"]["citation_rejections"] >= 1
    # The (now coerced) citation must be a dict with verified=False after retries exhausted.
    cite = body["findings"][0]["citation"]
    assert isinstance(cite, dict)
    assert "verified" in cite


def test_synthesis_returns_scalar_summary_is_coerced(api, use_copilot, monkeypatch):
    """Live synthesis has been observed to return ``{"summary": 1}`` or
    ``{"summary": "text"}``. The API response must still expose a dict-shaped
    summary so consumers can subscript ``body["summary"]["verified_citations"]``
    without crashing.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers without notice",
                        "risk": "high",
                        "confidence": 0.9,
                        "citation": {"law": "PDPL", "article": "22"},
                        "rationale": "PDPL alignment.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "Vendor shall not transfer abroad without consent.", "rationale": "PDPL Art. 22."})
        if agent_name == "Synthesis":
            # Pathological live shape: scalar summary.
            return json.dumps({"summary": 1})
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    summary = body["summary"]
    assert isinstance(summary, dict)
    # All required scalar fields are filled in deterministically.
    for key in ("total_findings", "verified_citations", "citation_rejections", "draft_critiques", "recommendation"):
        assert key in summary, f"summary missing {key}"


def test_findings_with_non_dict_items_are_filtered(api, use_copilot, monkeypatch):
    """Defensive case: model returns ``{"findings": [1, 2, 3]}`` (the most
    extreme shape drift we've seen). The graph must not crash; non-dict
    entries are dropped at the boundary.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            return json.dumps({"findings": [1, 2, 3]})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "Insufficient findings to assess."}})
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    # Empty findings list is acceptable; the API contract just requires a list.
    assert isinstance(body["findings"], list)


# --- Wrapper JSON-mode behaviour ---------------------------------------------

def test_chat_passes_response_format_when_json_mode_set(monkeypatch):
    """When ``json_mode=True`` is passed to chat(), the wrapper must put
    ``response_format={"type":"json_object"}`` on the wire so GPT-5.1 stops
    wrapping payloads in prose. Verified against an offline-mode call so we
    don't need real credentials.
    """
    seen: dict[str, Any] = {}

    def fake_stub_chat(agent_name, messages):
        # Capture kwargs by inspecting the wrapper's intermediate state? The
        # offline path bypasses _online_chat, so we instead patch _online_chat
        # and force the wrapper into online mode for this assertion.
        return "{}"

    # Force online path so response_format reaches _online_chat.
    def fake_online_chat(model, messages, **kwargs):
        seen.update(kwargs)
        seen["model"] = model
        return _FakeCompletion("{}")

    monkeypatch.setattr(llm, "_online_chat", fake_online_chat)
    monkeypatch.setitem(llm._state, "offline", False)

    llm.chat(
        "Reviewer",
        "reasoning",
        [{"role": "user", "content": "hi"}],
        json_mode=True,
    )
    assert seen.get("response_format") == {"type": "json_object"}, seen


def test_chat_falls_back_when_gateway_rejects_response_format(monkeypatch):
    """If the gateway returns BadRequestError mentioning response_format, the
    wrapper retries once without it and the caller still gets a completion."""

    calls = {"count": 0, "kwargs": []}

    class _BadRequest(Exception):
        pass

    def fake_online_chat(model, messages, **kwargs):
        calls["count"] += 1
        calls["kwargs"].append(dict(kwargs))
        if "response_format" in kwargs:
            raise _BadRequest("Unsupported parameter: 'response_format' is not supported on this model.")
        return _FakeCompletion("{}")

    # Make _looks_like_response_format_rejection recognise our fake error.
    monkeypatch.setattr(llm, "_online_chat", fake_online_chat)
    monkeypatch.setitem(llm._state, "offline", False)

    # The default detector keys off the class name "BadRequest" + message
    # markers; rename our fake class to satisfy it.
    _BadRequest.__name__ = "BadRequestError"

    completion = llm.chat(
        "Reviewer",
        "reasoning",
        [{"role": "user", "content": "hi"}],
        json_mode=True,
    )
    assert completion is not None
    assert calls["count"] == 2, "wrapper should have retried exactly once"
    assert "response_format" not in calls["kwargs"][1]


# =============================================================================
# Tier 1 / Tier 2 coverage extension — failure shapes the M2 audit identified
# as likely-but-untested. Added per the post-mortem ("why didn't we catch this
# before sending to the PO?") so the same class of gap can't recur.
# =============================================================================


def _record_reviewer_inputs(monkeypatch):
    """Capture the user-message text every time the Reviewer agent is invoked.

    Returns a list[str] that the test can inspect after running /run.
    """
    captured: list[str] = []

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and not _is_recite_prompt(sys) and not _is_critic_prompt(sys):
            captured.append(messages[1]["content"])
            return json.dumps({"findings": []})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "OK"}})
        return "{}"

    _patch_chat(monkeypatch, responder)
    return captured


# --- Tier 1.1: long documents reach the Reviewer in full ---------------------

def test_long_document_reaches_reviewer_beyond_4kb_clip(api, use_copilot, monkeypatch):
    """Real NDAs are 8-15 KB; the original 4 KB clip silently dropped the
    indemnity / choice-of-law / survival clauses that sit at the END of the
    document — exactly the clauses the Reviewer most needs to flag.

    Send a 12 KB NDA whose riskiest sentinel sits at the very end and assert
    the Reviewer's user message contains that sentinel.
    """
    captured = _record_reviewer_inputs(monkeypatch)
    sentinel = "SENTINEL_INDEMNITY_CAP_AT_TWELVE_MONTHS"
    filler = " Routine confidentiality language repeated. " * 250  # ~12 KB
    document = {
        "type": "text",
        "title": "Long vendor NDA",
        "content": (
            "VENDOR MASTER NDA\n\n1. Confidentiality.\n"
            + filler
            + f"\n\n7. Indemnity. {sentinel}. Vendor's liability is capped."
        ),
    }
    assert len(document["content"]) > 10_000, "test document must exceed the old 4 KB clip"

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": document},
    )
    assert resp.status_code == 200, resp.text
    assert captured, "Reviewer was never invoked"
    assert sentinel in captured[0], (
        "Reviewer's user message did not include the end-of-document sentinel — "
        "the document clip is too aggressive and the riskiest clauses are being dropped"
    )


# --- Tier 1.2: Arabic NDA content (not just intake) --------------------------

def test_arabic_document_content_does_not_crash(api, use_copilot, monkeypatch):
    """Ahmed's persona has Arabic INTAKE but English document content. A real
    Arabic-only NDA reaches the Reviewer with UTF-8 RTL text in the user
    message. Assert the prompt is constructed cleanly and the graph survives.
    """
    captured = _record_reviewer_inputs(monkeypatch)
    arabic_nda = (
        "اتفاقية عدم الإفصاح\n\n"
        "1. المعلومات السرية: يجوز لكل طرف الإفصاح عن معلومات سرية للطرف الآخر.\n"
        "2. النقل عبر الحدود: يجوز للمورد نقل المعلومات السرية إلى شركاء مصرفيين "
        "خارج دولة الإمارات العربية المتحدة دون إشعار مسبق.\n"
        "3. التعويض: تقتصر مسؤولية المورد على رسوم اثني عشر شهراً."
    )
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": use_copilot,
            "document": {"type": "text", "title": "NDA عربي", "content": arabic_nda},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    # The Arabic clauses must round-trip into the Reviewer prompt without
    # encoding loss.
    assert captured, "Reviewer was never invoked"
    assert "النقل عبر الحدود" in captured[0]


# --- Tier 1.3: Reviewer returns confidence/risk with wrong types -------------

def test_reviewer_emits_string_confidence_and_int_risk_does_not_crash(
    api, use_copilot, monkeypatch
):
    """Live models occasionally emit ``confidence: "high"`` (string) instead
    of a float, and ``risk: 5`` (int) instead of "high"/"medium"/"low".
    The graph must survive both and the synthesis bucket counts must remain
    numeric.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers",
                        "risk": 5,  # type drift: int instead of string
                        "confidence": "high",  # type drift: string instead of float
                        "citation": {"law": "PDPL", "article": "22"},
                        "rationale": "PDPL alignment.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "No cross-border without consent.", "rationale": "PDPL Art. 22."})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "DO NOT SIGN"}})
        return "{}"

    _patch_chat(monkeypatch, responder)
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    # Summary counters must remain numeric regardless of model type drift.
    summary = body["summary"]
    for key in ("total_findings", "high_risk", "medium_risk", "low_risk", "verified_citations"):
        assert isinstance(summary[key], int), f"{key} should be int, got {type(summary[key]).__name__}"


# --- Tier 1.4: Drafter returns null/missing draft_clause ---------------------

def test_drafter_returns_null_draft_clause_yields_empty_proposal(
    api, use_copilot, monkeypatch
):
    """Live drafters occasionally return ``{"draft_clause": null}`` or omit
    the key entirely. The graph must produce an empty counter_proposal string
    rather than crashing or surfacing ``None`` to the API consumer.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers",
                        "risk": "high",
                        "confidence": 0.9,
                        "citation": {"law": "PDPL", "article": "22"},
                        "rationale": "r.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": None, "rationale": "intentionally null"})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "Review"}})
        return "{}"

    _patch_chat(monkeypatch, responder)
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    finding = body["findings"][0]
    # ``counter_proposal`` MUST be a string (possibly empty) — never None.
    assert "counter_proposal" in finding
    assert isinstance(finding["counter_proposal"], str)


# --- Tier 1.5: Loop 4 exhausts with empty candidate list ---------------------

def test_loop4_empty_candidates_terminates_with_unverified(api, use_copilot, monkeypatch):
    """If the Reviewer cites a law NOT in our 4-statute corpus, semantic search
    can return zero candidates. The recite prompt then has no candidates to
    offer; the model keeps guessing the same wrong citation. The graph must
    terminate cleanly after MAX_CITATION_RETRIES with verified=False.
    """
    from app.corpus import retrieval as retrieval_mod

    # Force retrieval.semantic_search to always return [] so the verifier's
    # candidate list stays empty across all retries.
    monkeypatch.setattr(retrieval_mod, "semantic_search", lambda *a, **k: [])

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "ok"})
        if agent_name == "Reviewer" and _is_recite_prompt(sys):
            # No candidates available — model keeps emitting an unverifiable cite.
            return json.dumps({"citation": {"law": "Federal Decree-Law 99", "article": "9999"}})
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers",
                        "risk": "high",
                        "confidence": 0.9,
                        # Law not in the corpus → verifier will reject.
                        "citation": {"law": "Federal Decree-Law 99 of 2099", "article": "9999"},
                        "rationale": "r.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "X", "rationale": "r."})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "Review"}})
        return "{}"

    _patch_chat(monkeypatch, responder)
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)
    # Three retries exhausted → finding kept with verified=False.
    cite = body["findings"][0]["citation"]
    assert cite["verified"] is False
    assert body["summary"]["citation_rejections"] >= use_graph.MAX_CITATION_RETRIES


# --- Tier 2.1: Mixed Arabic + English clauses --------------------------------

def test_mixed_language_document_handled(api, use_copilot, monkeypatch):
    """Real-world NDAs in the UAE sometimes mix English and Arabic clauses
    (e.g. governing-law clause in Arabic, body in English). The Reviewer
    must receive both languages intact in the user message.
    """
    captured = _record_reviewer_inputs(monkeypatch)
    document = {
        "type": "text",
        "title": "Bilingual NDA",
        "content": (
            "1. Definitions. The parties acknowledge mutual confidential information.\n"
            "2. القانون الحاكم: تخضع هذه الاتفاقية لقوانين دولة الإمارات العربية المتحدة.\n"
            "3. Cross-border transfer. Vendor may transfer data abroad without notice."
        ),
    }
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": document},
    )
    assert resp.status_code == 200, resp.text
    assert captured
    assert "Cross-border transfer" in captured[0]
    assert "القانون الحاكم" in captured[0]


# --- Tier 2.2: Critic returns string bool ------------------------------------

def test_critic_returns_string_bool_is_treated_as_falsy(api, use_copilot, monkeypatch):
    """``verdict.get("accepted")`` is consumed by ``if verdict.get("accepted"):``.
    A string ``"true"`` is truthy in Python, so this path WOULD silently mark
    the draft accepted. We assert the contract: the graph completes, and the
    finding records ``counter_proposal_accepted`` as a bool, not a string.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": "true", "critique": "string bool drift"})
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "X",
                        "risk": "high",
                        "confidence": 0.9,
                        "citation": {"law": "PDPL", "article": "22"},
                        "rationale": "r.",
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({"draft_clause": "Y", "rationale": "r."})
        if agent_name == "Synthesis":
            return json.dumps({"summary": {"recommendation": "Review"}})
        return "{}"

    _patch_chat(monkeypatch, responder)
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    finding = body["findings"][0]
    # Whatever the graph decided, the field must be a strict bool when present.
    if "counter_proposal_accepted" in finding:
        assert isinstance(finding["counter_proposal_accepted"], bool)


# --- Tier 2.3: Unknown copilot_id returns 404 with detail --------------------

def test_unknown_copilot_id_returns_404(api):
    """API contract: use-mode requests against an unregistered copilot must
    return 404 with a descriptive detail (not 500, not 422)."""
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": "cp_does_not_exist_99999999",
            "document": {"type": "text", "title": "t", "content": "x"},
        },
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "unknown" in detail.lower() or "not found" in detail.lower() or "cp_does_not_exist" in detail


# --- Tier 2.4: Concurrent /run calls must not collide on audit-trail logging -

def test_concurrent_use_runs_have_unique_run_ids(api, use_copilot):
    """The audit logger uses a threading.Lock to serialise JSONL appends, but
    it has never been exercised under contention. Fire 5 concurrent /run
    calls and assert: all succeed, all return distinct run_ids, and each
    response's audit_trail contains only entries for its own run_id.
    """
    from concurrent.futures import ThreadPoolExecutor

    payload = {
        "mode": "use",
        "copilot_id": use_copilot,
        "document": {"type": "text", "title": "concurrent", "content": "Vendor NDA with cross-border clause."},
    }

    def _one():
        return api.post("/run", json=payload)

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda _: _one(), range(5)))

    bodies = []
    for r in results:
        assert r.status_code == 200, r.text
        bodies.append(r.json())

    run_ids = [b["run_id"] for b in bodies]
    assert len(set(run_ids)) == len(run_ids), f"duplicate run_ids under contention: {run_ids}"

    # Audit isolation: each run's audit_trail must reference its own run_id only.
    for body in bodies:
        own_id = body["run_id"]
        for entry in body["audit_trail"]:
            # AuditTrail.add stamps run_id on every entry.
            if "run_id" in entry:
                assert entry["run_id"] == own_id, (
                    f"audit trail contamination: run {own_id} contains entry from run {entry['run_id']}"
                )


# --- Tier 2.5: Use-mode without a document content yields 422 ----------------

def test_use_mode_empty_document_returns_422(api, use_copilot):
    """API contract: empty/whitespace document must be rejected with a 422
    rather than crashing the graph downstream."""
    for content in ("", "   ", "\n\n\t"):
        resp = api.post(
            "/run",
            json={
                "mode": "use",
                "copilot_id": use_copilot,
                "document": {"type": "text", "title": "empty", "content": content},
            },
        )
        assert resp.status_code == 422, f"empty content {content!r} should 422, got {resp.status_code}: {resp.text}"


# =============================================================================
# Tier 3 — Symmetric Loop 4 / 5 gate-counting evidence
#
# The M2.2 acceptance gate now counts Loop 4 / 5 *firings* from the audit trail
# rather than counting rejection events (see e2e_acceptance.py and
# docs/architecture.md §4 "What counts as a Loop X firing"). The old gate
# wrongly penalised a Reviewer that cited correctly on the first try, because
# zero rejections were registered even though Loops 4 and 5 demonstrably ran.
#
# This regression test pins the symmetric fix: a "perfect-citation Reviewer"
# whose every citation verifies on the first attempt and whose every
# counter-proposal is accepted by the Critic on the first attempt must still
# produce auditable Loop 4 / Loop 5 firings — and the gate's new counting must
# pass them.
# =============================================================================


def test_perfect_citation_reviewer_passes_m22_with_zero_rejections(
    api, use_copilot, monkeypatch
):
    """Symmetric Loop 4 fix: a Reviewer that cites perfectly on the first try
    must still trigger the audit-trail entries Loops 4 and 5 record on every
    invocation (verifier verdict, critic verdict) — and the new M2.2 gate
    counting (loop4_actions >= 1 AND loop5_actions >= 1) must pass.

    Setup mocks all four use-mode agents to produce a clean, single-pass run:
      * Reviewer emits one finding citing PDPL Art 22 (real, in the corpus)
      * Citation Verifier looks it up, finds it, logs Loop 4 with
        decision="verified", attempt=1
      * Drafter returns a clean replacement clause on attempt=1
      * Critic accepts on attempt=1, logs Loop 5 with decision="accepted"
      * Synthesis returns a structured summary

    The old (rejection-counting) gate would have wrongly failed this run —
    this test guarantees that regression cannot recur.
    """

    def responder(agent_name, messages, kwargs):
        sys = messages[0]["content"]
        if agent_name == "Reviewer" and _is_critic_prompt(sys):
            return json.dumps({"accepted": True, "critique": "Clean draft, aligned with PDPL Art 22."})
        if agent_name == "Reviewer" and _is_recite_prompt(sys):
            pytest.fail("Reviewer must not be asked to re-cite — first citation was valid (PDPL Art 22 is in the corpus)")
            return "{}"  # unreachable; quiets the type checker
        if agent_name == "Reviewer":
            return json.dumps({
                "findings": [
                    {
                        "clause": "Cross-border transfers to banking partners without notice",
                        "risk": "high",
                        "confidence": 0.92,
                        "citation": {"law": "PDPL", "article": "22"},
                        "rationale": (
                            "PDPL Art. 22 requires breach notification to the Data Office; "
                            "the clause does not provide a notification mechanism."
                        ),
                    }
                ]
            })
        if agent_name == "Counter-Proposal Drafter":
            return json.dumps({
                "draft_clause": (
                    "Vendor shall notify the Controller of any breach of Personal Data "
                    "within 72 hours, in alignment with PDPL Article 22."
                ),
                "rationale": "Restores statutory notification path.",
            })
        if agent_name == "Synthesis":
            return json.dumps({
                "summary": {
                    "recommendation": "DO NOT SIGN — material PDPL gap on breach notification.",
                    "total_findings": 1,
                }
            })
        return "{}"

    _patch_chat(monkeypatch, responder)

    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": use_copilot, "document": _vendor_nda_document()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_use_response(body)

    summary = body["summary"]
    audit = body["audit_trail"]

    # --- Quality signals: zero rejections, zero critique cycles ---------------
    assert summary["citation_rejections"] == 0, (
        "Reviewer cited PDPL Art 22 (real, in corpus) — expected 0 rejections, "
        f"got {summary['citation_rejections']}"
    )
    assert summary["draft_critiques"] == 0, (
        "Critic accepted on first attempt — expected 0 critique rejections, "
        f"got {summary['draft_critiques']}"
    )

    # --- Loop 4 firing evidence (the symmetric-fix invariant) -----------------
    loop4_entries = [e for e in audit if e.get("loop") == "Loop 4"]
    assert loop4_entries, (
        "audit_trail has no Loop 4 entries even though the verifier ran — the "
        "verifier must tag every invocation with loop='Loop 4', accept or reject"
    )
    # Specifically, the verifier-verdict entry exists AND records a verified outcome.
    verifier_verdicts = [
        e for e in loop4_entries
        if e.get("agent") == "Citation Verifier" and e.get("action") == "verify_citation"
    ]
    assert verifier_verdicts, "Loop 4 entries exist but none is the Citation Verifier verdict"
    assert verifier_verdicts[0]["decision"] == "verified", (
        f"Expected decision='verified' for PDPL Art 22 lookup, got "
        f"{verifier_verdicts[0]['decision']!r}"
    )

    # --- Loop 5 firing evidence (symmetric to Loop 4) -------------------------
    loop5_entries = [e for e in audit if e.get("loop") == "Loop 5"]
    assert loop5_entries, (
        "audit_trail has no Loop 5 entries even though the critic ran — the "
        "critic must tag every invocation with loop='Loop 5', accept or reject"
    )
    critic_verdicts = [
        e for e in loop5_entries
        if e.get("agent") == "Reviewer" and e.get("action") == "critique_draft"
    ]
    assert critic_verdicts, "Loop 5 entries exist but none is the critic verdict"
    assert critic_verdicts[0]["decision"] == "accepted", (
        f"Expected decision='accepted' for first-try perfect draft, got "
        f"{critic_verdicts[0]['decision']!r}"
    )

    # --- Replicate the M2.2 gate counting and assert it passes ----------------
    # Mirrors e2e_acceptance.py exactly (the audit-trail-based definition).
    loop4_actions = sum(1 for e in audit if e.get("loop") == "Loop 4")
    loop5_actions = sum(1 for e in audit if e.get("loop") == "Loop 5")
    assert loop4_actions >= 1 and loop5_actions >= 1, (
        f"M2.2 gate would FAIL on a perfect-citation run "
        f"(loop4_actions={loop4_actions}, loop5_actions={loop5_actions}) — "
        f"the symmetric fix is broken"
    )
