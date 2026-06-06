"""PO acceptance-review E2E tests — assert the Milestone 1 gates.

These mirror the founder/Claude acceptance review: each test maps to one of the
six Build-mode acceptance gates and fails loudly if the gate is not met.
"""
from __future__ import annotations

import pytest

from helpers import CUSTOMER_SCENARIOS, assert_valid_build_response, loops_in


def test_gate1_llm_connection_health(api):
    """Gate 1: the LLM client is wired and reports its configuration."""
    resp = api.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["models"]) >= {"standard", "reasoning", "embedding"}


def test_gate2_corpus_ingested_and_retrievable():
    """Gate 2: corpus ingested (4 statutes per PRD §5) and retrieval returns hits."""
    from app.corpus.retrieval import get_article, is_ingested, semantic_search

    assert is_ingested(), "corpus collection is empty"
    hits = semantic_search("personal data cross border transfer consent", top_k=3)
    assert hits, "no retrieval hits"
    laws = {h["metadata"]["short_name"] for h in hits}
    assert laws & {"PDPL", "Labour Law", "Commercial Transactions Law", "Civil Transactions Law"}, laws
    for h in hits:
        assert {"law_name", "short_name", "article_number"} <= set(h["metadata"])
    # Exact-text lookup must work for all four statutes — the Citation Verifier
    # (Loop 4) depends on this for hallucinated-citation rejection.
    for short_name, article in [
        ("Labour Law", "43"),
        ("PDPL", "7"),
        ("Commercial Transactions Law", "87"),
        ("Civil Transactions Law", "246"),
    ]:
        hit = get_article(short_name, article)
        assert hit is not None, f"exact-text lookup failed for {short_name} Art {article}"


def test_gate2_retrieval_is_semantic_when_live(offline):
    """When live (real embeddings), retrieval is semantically correct."""
    if offline:
        pytest.skip("Offline embeddings are hash-based, not semantic.")
    from app.corpus.retrieval import semantic_search

    top = semantic_search("notice period to terminate employment", top_k=1)[0]
    assert top["metadata"]["short_name"] == "Labour Law"


def test_gate3_build_returns_valid_response(api):
    """Gate 3: POST /run mode=build returns a valid response on a sample input."""
    sample = CUSTOMER_SCENARIOS[0]["intake"]
    resp = api.post("/run", json={"mode": "build", "intake": sample})
    assert resp.status_code == 200, resp.text
    assert_valid_build_response(resp.json())


def test_gate3_build_handles_arabic(api):
    """Gate 3 (Arabic): an Arabic input also returns a valid response."""
    arabic = next(s for s in CUSTOMER_SCENARIOS if s["id"] == "ahmed_arabic")
    resp = api.post("/run", json={"mode": "build", "intake": arabic["intake"]})
    assert resp.status_code == 200, resp.text
    assert_valid_build_response(resp.json())


def test_gate5_loops_fire_and_are_logged(api, offline):
    """Gate 5: Loops 1-3 fire and appear in the audit trail."""
    resp = api.post("/run", json={"mode": "build", "intake": {"workflow_description": "review NDAs", "language": "en"}})
    assert resp.status_code == 200, resp.text
    fired = loops_in(resp.json()["audit_trail"])
    if offline:
        assert {"Loop 1", "Loop 2", "Loop 3"}.issubset(fired), fired
    else:
        # Live: at least the debate + validation loops must be observable.
        assert {"Loop 1", "Loop 2"}.issubset(fired), fired


def test_gate5_llm_calls_logged():
    """Every LLM call is logged with the required audit fields (SOW section 6)."""
    import json

    from app import config

    log = config.LOG_DIR / "llm_calls.jsonl"
    assert log.exists(), "llm_calls.jsonl missing"
    last = [json.loads(l) for l in log.read_text().splitlines() if l.strip()][-1]
    required = {
        "timestamp", "agent", "model", "tier", "latency_seconds",
        "input_tokens", "output_tokens", "status",
    }
    assert required <= set(last), set(last)


# --- Milestone 2 gates --------------------------------------------------------

def test_gate7_use_mode_returns_verified_citations_on_three_examples(api, offline):
    """M2 Gate 7 (Amendment §3 criterion 1): POST /run mode=use returns valid
    responses with verified citations on all 3 use-mode example inputs.
    """
    import json
    from pathlib import Path

    from helpers import USE_MODE_SCENARIOS, assert_valid_use_response, fresh_copilot

    if not offline:
        pytest.skip("Live citation matching depends on the configured model.")

    copilot_id = fresh_copilot(api)
    root = Path(__file__).resolve().parent.parent
    for scenario in USE_MODE_SCENARIOS:
        case = json.loads((root / scenario["input_file"]).read_text(encoding="utf-8"))
        resp = api.post(
            "/run",
            json={"mode": "use", "copilot_id": copilot_id, "document": case["document"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_valid_use_response(body)
        assert len(body["findings"]) >= scenario["expect_min_findings"], (
            f"{scenario['id']}: expected >= {scenario['expect_min_findings']} findings, "
            f"got {len(body['findings'])}"
        )
        for f in body["findings"]:
            assert f["citation"].get("verified") is True, (
                f"{scenario['id']}: unverified citation in committed finding {f}"
            )
        assert body["summary"]["verified_citations"] == len(body["findings"])
        assert body["summary"]["recommendation"].startswith(
            scenario["expect_recommendation_prefix"]
        ), body["summary"]["recommendation"]


def test_gate8_loops_4_and_5_demonstrably_fire(api, offline):
    """M2 Gate 8 (Amendment §3 criterion 2): Loops 4 and 5 fire visibly in
    logs on test inputs — not merely present in code.
    """
    import json
    from pathlib import Path

    from helpers import loops_in, fresh_copilot

    if not offline:
        pytest.skip("Loop-4 hallucination injection is offline-stub behaviour.")

    copilot_id = fresh_copilot(api)
    root = Path(__file__).resolve().parent.parent
    case = json.loads(
        (root / "input_examples/use_mode/01_aggressive_vendor_nda.json").read_text(encoding="utf-8")
    )
    resp = api.post(
        "/run",
        json={"mode": "use", "copilot_id": copilot_id, "document": case["document"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fired = loops_in(body["audit_trail"])
    assert {"Loop 4", "Loop 5"}.issubset(fired), fired
    # Loop 4 must contain at least one rejected verdict; Loop 5 at least one rejected critique.
    rejections = [
        e for e in body["audit_trail"]
        if e.get("loop") == "Loop 4" and e.get("decision") == "rejected"
    ]
    critiques = [
        e for e in body["audit_trail"]
        if e.get("loop") == "Loop 5" and e.get("decision") == "rejected"
    ]
    assert rejections, "Loop 4 fired without a single rejected citation"
    assert critiques, "Loop 5 fired without a single rejected draft"
    assert body["summary"]["citation_rejections"] >= 1
    assert body["summary"]["draft_critiques"] >= 1


def test_gate8_canonical_sample_log_committed():
    """The canonical use-mode sample log (Loops 4-5 evidence) is committed to logs/samples/."""
    import json
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "logs" / "samples" / "use_mode_run_loops_4_5.jsonl"
    assert sample.exists(), f"missing committed sample: {sample}"
    entries = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines() if line.strip()]
    loops = {e.get("loop") for e in entries if e.get("loop")}
    assert {"Loop 4", "Loop 5"}.issubset(loops), loops


def test_arabic_interviewer_handles_hospital_input(api, offline):
    """Amendment §3 criterion 4: Arabic input works on the Interviewer,
    verified against input_examples/build_02_hospital_nda_ar.json."""
    import json
    from pathlib import Path

    from helpers import assert_valid_build_response, has_arabic

    if not offline:
        pytest.skip("Arabic-reply fidelity depends on the live model.")

    case = json.loads(
        (Path(__file__).resolve().parent.parent / "input_examples" / "build_02_hospital_nda_ar.json")
        .read_text(encoding="utf-8")
    )
    resp = api.post("/run", json={"mode": "build", "intake": case["intake"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_build_response(body)
    assert has_arabic(body.get("interviewer_response", "")), body.get("interviewer_response")


# --- Compass / GPT-5 parameter compatibility (regression) -------------------

def test_chat_never_sends_max_tokens_or_injects_cap(monkeypatch):
    """Regression for two PO-confirmed Compass invariants:

    1. The wrapper must NEVER send the legacy `max_tokens` to the SDK call —
       GPT-5 / o-series reasoning models on Compass reject it.
    2. The wrapper must NOT inject any token cap of its own by default —
       Compass enforces group-level quotas, not per-request limits, so the
       old SAMPLE_MODE auto-injection was unnecessary and is now removed.

    The wrapper still defensively translates a caller-supplied legacy
    `max_tokens` kwarg into `max_completion_tokens` for back-compat.
    """
    from app import llm

    captured: dict = {}

    class _FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _FakeCompletion:
        choices = [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]
        usage = _FakeUsage()
        model = "gpt-5.1"

    class _FakeChatCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeChatCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(llm, "_client", _FakeClient(), raising=False)
    monkeypatch.setitem(llm._state, "offline", False)

    # 1. Default call: no token cap of any name should hit the SDK.
    llm.chat("Reviewer", "reasoning", [{"role": "user", "content": "test"}])
    assert "max_tokens" not in captured, (
        "wrapper leaked legacy 'max_tokens' to OpenAI SDK; Compass GPT-5.1 will reject it"
    )
    assert "max_completion_tokens" not in captured, (
        "wrapper auto-injected a token cap; Compass uses group-level quotas, "
        "no per-request cap should be sent"
    )

    # 2. SAMPLE_MODE=true must also NOT cause an injection — the historical
    #    behaviour has been removed per PO clarification.
    captured.clear()
    monkeypatch.setattr(llm.config, "SAMPLE_MODE", True)
    llm.chat("Reviewer", "reasoning", [{"role": "user", "content": "test"}])
    assert "max_tokens" not in captured and "max_completion_tokens" not in captured, captured

    # 3. Caller-provided legacy max_tokens must be translated, not passed through.
    captured.clear()
    llm.chat("Reviewer", "reasoning", [{"role": "user", "content": "x"}], max_tokens=42)
    assert "max_tokens" not in captured
    assert captured.get("max_completion_tokens") == 42

    # 4. Caller-provided max_completion_tokens flows through unchanged.
    captured.clear()
    llm.chat("Reviewer", "reasoning", [{"role": "user", "content": "x"}], max_completion_tokens=99)
    assert captured.get("max_completion_tokens") == 99
