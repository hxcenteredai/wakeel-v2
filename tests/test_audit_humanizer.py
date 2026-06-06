"""Tests for the audit-trail humanizer (``app.audit_humanizer``).

Coverage strategy:
  - Every (agent, action, decision) tuple that the agent council emits in
    build_agents.py / use_agents.py / build_graph.py / use_graph.py is mapped
    to a business-readable line.
  - The fallback path must never crash on an unknown or malformed entry —
    the audit panel must keep rendering even if a new agent is added later.
"""
from __future__ import annotations

from app.audit_humanizer import humanize


def _entry(**kwargs):
    base = {
        "agent": "",
        "action": "",
        "decision": "",
        "reason": "",
        "loop": None,
        "details": {},
    }
    base.update(kwargs)
    return base


def test_build_run_start():
    assert humanize(_entry(agent="orchestrator", action="run_start", decision="build")) == "▶ Build run started"


def test_use_run_start_includes_copilot_id():
    label = humanize(
        _entry(agent="orchestrator", action="run_start", decision="use", details={"copilot_id": "cp_abc"})
    )
    assert "Use run started" in label
    assert "cp_abc" in label
    assert label.startswith("▶")


def test_run_complete():
    assert humanize(_entry(agent="orchestrator", action="run_complete", decision="ok")) == "▶ Run complete"


def test_persist_copilot_error_warns():
    label = humanize(_entry(agent="orchestrator", action="persist_copilot", decision="error"))
    assert label.startswith("⚠")
    assert "persistence" in label.lower()


def test_interviewer_first_pass_structured():
    label = humanize(_entry(agent="Interviewer", action="extract_requirements", decision="structured"))
    assert label.startswith("✓")
    assert "Interview captured" in label


def test_interviewer_needs_clarification_is_informational():
    label = humanize(_entry(agent="Interviewer", action="extract_requirements", decision="needs_clarification"))
    assert label.startswith("ℹ")
    assert "clarification" in label.lower()


def test_interviewer_loop3_structured_marks_recovery():
    label = humanize(
        _entry(agent="Interviewer", action="extract_requirements", decision="structured", loop="Loop 3")
    )
    assert label.startswith("✓")
    assert "Loop 3" in label


def test_interviewer_loop3_still_needs_clarification():
    label = humanize(
        _entry(
            agent="Interviewer",
            action="extract_requirements",
            decision="needs_clarification",
            loop="Loop 3",
        )
    )
    assert "Loop 3" in label
    assert "clarification" in label.lower()


def test_debater_a_strict_compliance():
    label = humanize(
        _entry(
            agent="Debater A",
            action="debate_argument",
            decision="strict_compliance",
            loop="Loop 1",
            details={"arguments": ["a", "b", "c"]},
        )
    )
    assert "Strict Compliance" in label
    assert "Debater A" in label
    assert "3 arguments" in label
    assert label.startswith("✓")


def test_debater_b_business_practical():
    label = humanize(
        _entry(
            agent="Debater B",
            action="debate_argument",
            decision="business_practicality",
            loop="Loop 1",
            details={"arguments": []},
        )
    )
    assert "Business Practical" in label
    assert "Debater B" in label


def test_architect_synthesize():
    label = humanize(_entry(agent="Architect", action="synthesize_or_escalate", decision="synthesize"))
    assert "Synthesis complete" in label
    assert label.startswith("✓")


def test_architect_loop1_more_debate():
    label = humanize(
        _entry(
            agent="Architect",
            action="synthesize_or_escalate",
            decision="request_more_debate",
            loop="Loop 1",
        )
    )
    assert label.startswith("⟳")
    assert "Loop 1" in label
    assert "Architect" in label


def test_architect_loop3_needs_clarification():
    label = humanize(
        _entry(
            agent="Architect",
            action="synthesize_or_escalate",
            decision="needs_clarification",
            loop="Loop 3",
        )
    )
    assert label.startswith("⟳")
    assert "Loop 3" in label


def test_builder_includes_copilot_id():
    label = humanize(
        _entry(
            agent="Builder",
            action="instantiate_copilot",
            decision="built",
            details={"copilot_id": "cp_xyz123", "template": "nda_review"},
        )
    )
    assert label.startswith("✓")
    assert "cp_xyz123" in label


def test_validator_passed_shows_score():
    label = humanize(
        _entry(
            agent="Validator",
            action="validate_copilot",
            decision="passed",
            loop="Loop 2",
            details={"score": 0.91, "iteration": 1, "issues": []},
        )
    )
    assert label.startswith("✓")
    assert "0.91" in label
    assert "passed" in label.lower()


def test_validator_rejected_loop2():
    label = humanize(
        _entry(
            agent="Validator",
            action="validate_copilot",
            decision="rejected",
            loop="Loop 2",
            details={"score": 0.45, "iteration": 2, "issues": ["bad schema"]},
        )
    )
    assert label.startswith("⟳")
    assert "Loop 2" in label
    assert "0.45" in label
    assert "attempt 2" in label


def test_reviewer_findings_plural():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="review_document",
            decision="3 findings",
            details={"attempt": 1, "findings_count": 3},
        )
    )
    assert "3 risk findings" in label
    assert label.startswith("✓")


def test_reviewer_findings_singular():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="review_document",
            decision="1 findings",
            details={"attempt": 1, "findings_count": 1},
        )
    )
    assert "1 risk finding" in label
    assert "1 risk findings" not in label


def test_reviewer_no_findings():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="review_document",
            decision="0 findings",
            details={"attempt": 1, "findings_count": 0},
        )
    )
    assert "no risks" in label.lower()


def test_reviewer_findings_count_parsed_from_decision_when_missing_in_details():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="review_document",
            decision="5 findings",
            details={"attempt": 1},
        )
    )
    assert "5 risk findings" in label


def test_reviewer_re_cite_loop4():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="re_cite",
            decision="recited",
            loop="Loop 4",
            details={"attempt": 2},
        )
    )
    assert label.startswith("⟳")
    assert "Loop 4" in label
    assert "attempt 2" in label


def test_citation_verified_shows_law_and_article():
    label = humanize(
        _entry(
            agent="Citation Verifier",
            action="verify_citation",
            decision="verified",
            loop="Loop 4",
            details={"law": "Federal Decree-Law 45 of 2021", "article": "7"},
        )
    )
    assert label.startswith("✓")
    assert "Federal Decree-Law 45 of 2021" in label
    assert "Article 7" in label


def test_citation_rejected_loop4_callout():
    label = humanize(
        _entry(
            agent="Citation Verifier",
            action="verify_citation",
            decision="rejected",
            loop="Loop 4",
            details={"law": "PDPL", "article": "99"},
        )
    )
    assert label.startswith("⟳")
    assert "Loop 4" in label
    assert "PDPL" in label
    assert "99" in label
    assert "rejected" in label.lower()


def test_counter_proposal_first_pass_shows_clause_snippet():
    label = humanize(
        _entry(
            agent="Counter-Proposal Drafter",
            action="draft_counter_proposal",
            decision="drafted",
            details={"attempt": 1, "clause": "Confidentiality survives termination perpetually"},
        )
    )
    assert label.startswith("✓")
    assert "Counter-proposal drafted" in label
    assert "Confidentiality" in label


def test_counter_proposal_revision_loop5():
    label = humanize(
        _entry(
            agent="Counter-Proposal Drafter",
            action="draft_counter_proposal",
            decision="drafted",
            loop="Loop 5",
            details={"attempt": 3, "clause": "..."},
        )
    )
    assert label.startswith("⟳")
    assert "Loop 5" in label
    assert "attempt 3" in label


def test_critique_accepted_loop5():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="critique_draft",
            decision="accepted",
            loop="Loop 5",
            details={"attempt": 2, "accepted": True},
        )
    )
    assert label.startswith("✓")
    assert "accepted" in label.lower()
    assert "attempt 2" in label


def test_critique_rejected_loop5_drafter_revising():
    label = humanize(
        _entry(
            agent="Reviewer",
            action="critique_draft",
            decision="rejected",
            loop="Loop 5",
            details={"attempt": 1, "accepted": False},
        )
    )
    assert label.startswith("⟳")
    assert "Loop 5" in label
    assert "Drafter revising" in label


def test_synthesis_summarizes_loops():
    label = humanize(
        _entry(
            agent="Synthesis",
            action="assemble_response",
            decision="synthesised",
            details={"total_findings": 4, "citation_rejections": 2, "draft_critiques": 3},
        )
    )
    assert label.startswith("✓")
    assert "4 findings" in label
    assert "Loop 4 rejections: 2" in label
    assert "Loop 5 critiques: 3" in label


def test_synthesis_singular_no_loops():
    label = humanize(
        _entry(
            agent="Synthesis",
            action="assemble_response",
            decision="synthesised",
            details={"total_findings": 1, "citation_rejections": 0, "draft_critiques": 0},
        )
    )
    assert "1 finding finalized" in label
    assert "Loop 4" not in label
    assert "Loop 5" not in label


def test_fallback_for_unknown_agent_does_not_crash():
    label = humanize(
        _entry(agent="MysteryAgent", action="do_something", decision="weird", loop="Loop X")
    )
    assert "MysteryAgent" in label
    assert "do_something" in label
    assert "Loop X" in label
    assert "weird" in label


def test_empty_entry_does_not_raise():
    label = humanize({})
    assert isinstance(label, str)
    assert len(label) > 0


def test_partial_entry_with_only_agent():
    label = humanize({"agent": "Foo"})
    assert "Foo" in label
