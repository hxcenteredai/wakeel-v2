"""Customer-journey E2E tests — simulate real users building copilots.

Each scenario submits a build request exactly as a customer would through the
API and asserts the response is a valid, usable copilot build.
"""
from __future__ import annotations

import pytest

from helpers import (
    CUSTOMER_SCENARIOS,
    assert_valid_build_response,
    has_arabic,
    loops_in,
)


@pytest.mark.parametrize("scenario", CUSTOMER_SCENARIOS, ids=lambda s: s["id"])
def test_customer_can_build_copilot(api, offline, scenario):
    """A customer describes their workflow and receives a validated copilot."""
    resp = api.post("/run", json={"mode": "build", "intake": scenario["intake"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert_valid_build_response(body)

    fired = loops_in(body["audit_trail"])
    expected = scenario["expect_min_loops"]
    if offline and scenario.get("offline_expect_loops"):
        expected = scenario["offline_expect_loops"]
    assert expected.issubset(fired), f"expected loops {expected}, got {fired}"


def test_arabic_interviewer_replies_in_arabic(api, offline):
    """Arabic intake: the Interviewer responds in Arabic (deterministic offline)."""
    arabic = next(s for s in CUSTOMER_SCENARIOS if s["id"] == "ahmed_arabic")
    resp = api.post("/run", json={"mode": "build", "intake": arabic["intake"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_valid_build_response(body)

    if offline:
        # Stub Interviewer guarantees an Arabic reply; small live models may not.
        assert has_arabic(body.get("interviewer_response", "")), body.get("interviewer_response")
    else:
        pytest.skip("Live model Arabic-reply fidelity depends on the configured model.")


def test_vague_request_triggers_clarification_offline(api, offline):
    """Loop 3: an ambiguous request escalates back to the Interviewer."""
    if not offline:
        pytest.skip("Loop 3 firing live depends on model JSON compliance.")
    resp = api.post("/run", json={"mode": "build", "intake": {"workflow_description": "review NDAs", "language": "en"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Loop 3" in loops_in(body["audit_trail"]), "Loop 3 (clarification) did not fire"
    # The Interviewer should have run at least twice (initial + re-interview).
    interviews = [e for e in body["audit_trail"] if e["agent"] == "Interviewer"]
    assert len(interviews) >= 2, interviews


def test_use_mode_rejects_unknown_copilot(api):
    """Use mode (Milestone 2) returns 404 for a copilot_id that has never been built."""
    resp = api.post(
        "/run",
        json={
            "mode": "use",
            "copilot_id": "cp_does_not_exist",
            "document": {"type": "text", "content": "x"},
        },
    )
    assert resp.status_code == 404, resp.text
    assert "unknown copilot_id" in resp.json()["detail"]


def test_use_mode_requires_copilot_and_document(api):
    """Validate input contract for use mode."""
    resp = api.post("/run", json={"mode": "use", "document": {"type": "text", "content": "x"}})
    assert resp.status_code == 422, resp.text

    resp2 = api.post("/run", json={"mode": "use", "copilot_id": "cp_x"})
    assert resp2.status_code == 422, resp2.text


def test_build_requires_description(api):
    resp = api.post("/run", json={"mode": "build", "intake": {"workflow_description": "  ", "language": "en"}})
    assert resp.status_code == 422, resp.text
