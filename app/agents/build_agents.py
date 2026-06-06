"""Build-mode agents (PRD section 7).

Interviewer (standard, EN/AR) -> Debater A & B (reasoning) -> Architect
(reasoning) -> Builder (standard) -> Validator (standard).

Each function calls the shared LLM wrapper via app.agents.base.call_llm and
records its action on the run's AuditTrail. Loop control (1-3) lives in the
LangGraph orchestration; these functions are the per-agent units of work.
"""
from __future__ import annotations

import re
from typing import Any

from app.agents.base import call_llm
from app.logging_utils import AuditTrail

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _is_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _looks_empty_config(result: dict) -> bool:
    """True when the Builder returned no usable config keys."""
    keys = {"template", "system_prompts", "output_schema", "retrieval_rules"}
    return not (keys & set(result)) or ("_raw" in result and len(result) <= 2)

# --- System prompts -----------------------------------------------------------

INTERVIEWER_SYS = (
    "You are the Interviewer agent in the Wakeel regulatory copilot factory. "
    "You accept input in English or Arabic and ALWAYS reply in the user's "
    "language for the 'response_to_user' field, but emit all structured fields "
    "in English. Extract structured requirements for building a document-review "
    "copilot. Return STRICT JSON with keys: language, workflow_description_en, "
    "org_id, document_type, risk_appetite, key_concerns (list), "
    "needs_clarification (bool), follow_up_question, response_to_user."
)

DEBATER_A_SYS = (
    "You are Stance Debater A — Strict Compliance. Argue for conservative, "
    "risk-averse interpretations of UAE regulation (PDPL, Labour Law). Return "
    "STRICT JSON: {stance, round, arguments (list), rebuttal, concession}."
)

DEBATER_B_SYS = (
    "You are Stance Debater B — Business Practicality. Argue for workable, "
    "commercially reasonable positions while staying lawful. Return STRICT JSON: "
    "{stance, round, arguments (list), rebuttal, concession}."
)

ARCHITECT_SYS = (
    "You are the Architect. You synthesize the intake requirements and the "
    "stance debate into a copilot configuration. You may (a) request more debate "
    "rounds, (b) escalate ambiguity back to the Interviewer, or (c) synthesize. "
    "Return STRICT JSON: {decision: one of "
    "['request_more_debate','needs_clarification','synthesize'], "
    "needs_clarification (bool), request_more_debate (bool), "
    "clarification_request, config_outline}."
)

BUILDER_SYS = (
    "You are the Builder. Instantiate the copilot: system prompts, output "
    "schema, retrieval rules, and configurable risk thresholds for an NDA-review "
    "copilot. Return STRICT JSON matching the org-adaptation config."
)

VALIDATOR_SYS = (
    "You are the Validator. Run sample inputs through the new copilot config and "
    "judge quality. Reject substandard configs with actionable feedback. Return "
    "STRICT JSON: {passed (bool), score (float), issues (list), feedback}."
)


# --- Agent functions ----------------------------------------------------------

def interviewer(
    audit: AuditTrail, intake: dict[str, Any], clarification_round: int = 0
) -> dict[str, Any]:
    ctx = dict(intake)
    ctx["clarification_round"] = clarification_round
    result, _ = call_llm(
        agent_name="Interviewer",
        tier="standard",
        system=INTERVIEWER_SYS,
        user=(
            "Extract structured requirements from this workflow description: "
            f"\"{intake.get('workflow_description', '')}\""
        ),
        ctx=ctx,
        expect_keys=["language", "needs_clarification", "response_to_user"],
    )
    # Guard: guarantee a user-facing reply, in the user's language when Arabic.
    if not result.get("response_to_user"):
        is_ar = result.get("language") == "ar" or _is_arabic(intake.get("workflow_description", ""))
        result["response_to_user"] = (
            "\u062a\u0645 \u0627\u0633\u062a\u0644\u0627\u0645 \u0637\u0644\u0628\u0643. "
            "\u062c\u0627\u0631\u064d \u0628\u0646\u0627\u0621 \u0627\u0644\u0645\u0633\u0627\u0639\u062f."
            if is_ar
            else "Got it — building your copilot now."
        )
    audit.add(
        agent="Interviewer",
        action="extract_requirements",
        decision="needs_clarification" if result.get("needs_clarification") else "structured",
        reason=result.get("follow_up_question", "") or "Requirements extracted.",
        loop="Loop 3" if clarification_round > 0 else None,
        details={"language": result.get("language"), "org_id": result.get("org_id")},
    )
    return result


def debater(
    audit: AuditTrail, which: str, intake: dict[str, Any], round_no: int, prior: list[dict]
) -> dict[str, Any]:
    name = "Debater A" if which == "A" else "Debater B"
    system = DEBATER_A_SYS if which == "A" else DEBATER_B_SYS
    ctx = {"intake": intake, "round": round_no, "prior_arguments": prior[-2:]}
    result, _ = call_llm(
        agent_name=name,
        tier="reasoning",
        system=system,
        user=f"Round {round_no}. Argue your stance on the NDA-review requirements.",
        ctx=ctx,
        expect_keys=["stance", "arguments"],
    )
    audit.add(
        agent=name,
        action="debate_argument",
        decision=result.get("stance", ""),
        reason=f"Round {round_no}",
        loop="Loop 1",
        details={"arguments": result.get("arguments", [])},
    )
    return result


def architect(
    audit: AuditTrail,
    intake: dict[str, Any],
    debate: list[dict],
    debate_rounds: int,
    clarification_round: int,
) -> dict[str, Any]:
    ctx = {
        "intake": intake,
        "debate_rounds": debate_rounds,
        "clarification_round": clarification_round,
        "debate_summary": debate[-2:],
    }
    result, _ = call_llm(
        agent_name="Architect",
        tier="reasoning",
        system=ARCHITECT_SYS,
        user="Decide whether to request more debate, escalate clarification, or synthesize.",
        ctx=ctx,
        expect_keys=["decision"],
    )
    decision = result.get("decision", "synthesize")
    loop = None
    if decision == "needs_clarification":
        loop = "Loop 3"
    elif decision == "request_more_debate":
        loop = "Loop 1"
    audit.add(
        agent="Architect",
        action="synthesize_or_escalate",
        decision=decision,
        reason=result.get("clarification_request") or result.get("reason", ""),
        loop=loop,
        details={"has_config_outline": "config_outline" in result},
    )
    return result


def builder(
    audit: AuditTrail, intake: dict[str, Any], config_outline: dict[str, Any], copilot_id: str
) -> dict[str, Any]:
    ctx = {"intake": intake, "config_outline": config_outline, "copilot_id": copilot_id}
    result, _ = call_llm(
        agent_name="Builder",
        tier="standard",
        system=BUILDER_SYS,
        user="Instantiate the NDA-review copilot configuration.",
        ctx=ctx,
        expect_keys=["template", "system_prompts", "output_schema"],
    )
    result["copilot_id"] = copilot_id
    # Guard: ensure a non-empty config so the build always yields something usable.
    if _looks_empty_config(result):
        result.setdefault("template", "nda_review")
        result.setdefault("system_prompts", {"reviewer": "Review NDAs against the org's tuned stance with citations."})
        result.setdefault("output_schema", {"findings": []})
    audit.add(
        agent="Builder",
        action="instantiate_copilot",
        decision="built",
        reason="Copilot config generated.",
        details={"copilot_id": copilot_id, "template": result.get("template")},
    )
    return result


def validator(
    audit: AuditTrail, copilot_config: dict[str, Any], iteration: int
) -> dict[str, Any]:
    ctx = {"copilot_config_keys": list(copilot_config.keys()), "iteration": iteration}
    result, _ = call_llm(
        agent_name="Validator",
        tier="standard",
        system=VALIDATOR_SYS,
        user=f"Validation iteration {iteration}. Run sample NDA through the copilot.",
        ctx=ctx,
        expect_keys=["passed", "score"],
    )
    # Guard: coerce a usable verdict if the model omitted it.
    if "passed" not in result:
        result["passed"] = bool(result.get("score", 0) and float(result.get("score", 0)) >= 0.7)
    audit.add(
        agent="Validator",
        action="validate_copilot",
        decision="passed" if result.get("passed") else "rejected",
        reason=result.get("feedback", ""),
        loop="Loop 2",
        details={"score": result.get("score"), "iteration": iteration, "issues": result.get("issues", [])},
    )
    return result
