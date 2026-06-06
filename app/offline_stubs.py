"""Deterministic stub responses for OFFLINE_MODE.

These let the entire build-mode graph, all feedback loops, the UI, and the audit
logs run end-to-end with no Compass credentials and zero quota usage. Each agent
passes a small JSON "context" block (marker ``@@CTX@@``) in its prompt; the stub
reads it to produce coherent, schema-valid output and to drive loop behaviour
(e.g. the Validator rejects on iteration 1, accepts on iteration 2 to exercise
Loop 2). When real Compass creds are configured, OFFLINE_MODE is off and none of
this runs — the wrapper calls the live endpoint instead.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

CTX_MARKER = "@@CTX@@"
_EMBED_DIM = 256


def _extract_ctx(messages: list) -> dict[str, Any]:
    for msg in reversed(messages):
        content = str(msg.get("content", ""))
        if CTX_MARKER in content:
            raw = content.split(CTX_MARKER, 1)[1].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
    return {}


def _looks_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# --- Per-agent stub generators -------------------------------------------------

def _interviewer(ctx: dict[str, Any]) -> str:
    description = ctx.get("workflow_description", "")
    is_arabic = ctx.get("language") == "ar" or _looks_arabic(description)
    # Loop 3 support: when the Architect escalates, it sets clarification_round.
    clarification_round = ctx.get("clarification_round", 0)

    # Heuristic: treat very short or org-less descriptions as ambiguous on the
    # first pass so Loop 3 (requirements clarification) can demonstrably fire.
    has_org = bool(ctx.get("org_id")) or "org" in description.lower()
    ambiguous = (len(description.split()) < 12 or not has_org) and clarification_round == 0

    follow_up_en = (
        "To tailor the copilot, which organization is this for, and what is your "
        "risk appetite (conservative / balanced / permissive)?"
    )
    follow_up_ar = (
        "\u0644\u062a\u062e\u0635\u064a\u0635 \u0627\u0644\u0645\u0633\u0627\u0639\u062f\u060c "
        "\u0645\u0627 \u0647\u064a \u0627\u0644\u0645\u0624\u0633\u0633\u0629 \u0648\u0645\u0627 \u0645\u062f\u0649 "
        "\u062a\u062d\u0641\u0638\u0643 \u062a\u062c\u0627\u0647 \u0627\u0644\u0645\u062e\u0627\u0637\u0631\u061f"
    )

    result = {
        "language": "ar" if is_arabic else "en",
        "workflow_description_en": (
            "Review vendor NDAs against the organization's data-handling stance "
            "under UAE PDPL and Labour Law."
            if is_arabic
            else (description or "Review vendor NDAs against our data-handling stance.")
        ),
        "org_id": ctx.get("org_id") or "fintech_co_001",
        "document_type": "nda",
        "risk_appetite": ctx.get("risk_appetite") or "conservative",
        "key_concerns": [
            "data_transfer_abroad",
            "consent_reconfirmation",
            "confidentiality_duration",
        ],
        "needs_clarification": bool(ambiguous),
        "follow_up_question": (
            (follow_up_ar if is_arabic else follow_up_en) if ambiguous else ""
        ),
        "response_to_user": (
            (follow_up_ar if is_arabic else follow_up_en)
            if ambiguous
            else (
                "\u062a\u0645 \u0627\u0633\u062a\u0644\u0627\u0645 \u0637\u0644\u0628\u0643. "
                "\u062c\u0627\u0631\u064d \u0628\u0646\u0627\u0621 \u0627\u0644\u0645\u0633\u0627\u0639\u062f."
                if is_arabic
                else "Got it — building your copilot now."
            )
        ),
    }
    return _json(result)


def _debater_a(ctx: dict[str, Any]) -> str:
    rnd = ctx.get("round", 1)
    return _json(
        {
            "stance": "strict_compliance",
            "round": rnd,
            "arguments": [
                "PDPL Article 7 consent must be explicit and re-confirmed for any "
                "cross-border transfer to banking partners.",
                "Confidentiality obligations should survive termination indefinitely "
                "for personal data categories.",
                "Indemnity caps below full liability expose the org under PDPL breach "
                "notification duties.",
            ],
            "rebuttal": "Business convenience cannot override statutory consent duties.",
            "concession": "Standard commercial terms acceptable where no personal data flows.",
        }
    )


def _debater_b(ctx: dict[str, Any]) -> str:
    rnd = ctx.get("round", 1)
    return _json(
        {
            "stance": "business_practicality",
            "round": rnd,
            "arguments": [
                "Indefinite confidentiality is rarely accepted by vendors; a 5-year "
                "tail post-termination is market-standard and enforceable.",
                "Blanket re-consent on every transfer creates operational friction; "
                "a documented standing consent with audit log meets PDPL intent.",
                "Mutual indemnity caps at 12 months fees are commercially reasonable.",
            ],
            "rebuttal": "Over-conservative terms stall deals without reducing real risk.",
            "concession": "Cross-border transfers to non-adequate jurisdictions warrant explicit consent.",
        }
    )


def _architect(ctx: dict[str, Any]) -> str:
    debate_rounds = ctx.get("debate_rounds", 0)
    clarification_round = ctx.get("clarification_round", 0)
    intake = ctx.get("intake", {})

    # Loop 3: if the intake still flags clarification and we have not yet
    # escalated, ask the Interviewer for a follow-up (once).
    if intake.get("needs_clarification") and clarification_round == 0:
        return _json(
            {
                "decision": "needs_clarification",
                "needs_clarification": True,
                "clarification_request": "Confirm org_id and risk appetite before synthesis.",
                "request_more_debate": False,
            }
        )

    # Loop 1: request one extra debate round if fewer than 2 have run.
    if debate_rounds < 2:
        return _json(
            {
                "decision": "request_more_debate",
                "needs_clarification": False,
                "request_more_debate": True,
                "reason": "Need at least two rounds to resolve the cross-border consent split.",
            }
        )

    # Otherwise synthesize the config outline.
    return _json(
        {
            "decision": "synthesize",
            "needs_clarification": False,
            "request_more_debate": False,
            "config_outline": {
                "interpretation_overrides": [
                    {
                        "law": "Federal Decree-Law 45 of 2021",
                        "article": "Article 7",
                        "stance": "Require explicit consent re-confirmation for cross-border "
                        "transfers to banking partners.",
                        "rationale": "Conservative posture from strict-compliance debate.",
                    }
                ],
                "risk_thresholds": {
                    "high_flag_confidence_min": 0.80,
                    "medium_flag_confidence_min": 0.60,
                    "auto_escalate_to_human": ["data_transfer_abroad", "indemnity_caps"],
                },
                "confidentiality_tail_years": 5,
                "rationale": "Balanced synthesis: strict on PDPL consent, market-standard on tails.",
            },
        }
    )


def _builder(ctx: dict[str, Any]) -> str:
    intake = ctx.get("intake", {})
    outline = ctx.get("config_outline", {})
    org_id = intake.get("org_id", "org_default")
    return _json(
        {
            "copilot_id": ctx.get("copilot_id", "cp_pending"),
            "template": "nda_review",
            "org_id": org_id,
            "system_prompts": {
                "reviewer": "You review NDAs against the org's tuned PDPL/Labour stance. "
                "Flag clauses with confidence scores; cite exact articles.",
                "citation_verifier": "Verify every cited article against the corpus via "
                "exact-text retrieval; reject hallucinated citations.",
            },
            "output_schema": {
                "findings": [
                    {
                        "clause": "str",
                        "risk": "high|medium|low",
                        "confidence": "float",
                        "citation": {"law": "str", "article": "str"},
                        "rationale": "str",
                    }
                ]
            },
            "retrieval_rules": {
                "laws": [
                    "Federal Decree-Law 33 of 2021",
                    "Federal Decree-Law 45 of 2021",
                ],
                "top_k": 5,
                "chunk_by": "article",
            },
            "risk_thresholds": outline.get(
                "risk_thresholds",
                {"high_flag_confidence_min": 0.80, "medium_flag_confidence_min": 0.60},
            ),
            "interpretation_overrides": outline.get("interpretation_overrides", []),
        }
    )


def _validator(ctx: dict[str, Any]) -> str:
    iteration = ctx.get("iteration", 1)
    # Loop 2: reject the first build, accept the revised one.
    if iteration < 2:
        return _json(
            {
                "passed": False,
                "score": 0.62,
                "issues": [
                    "Reviewer prompt does not enforce citation on every regulatory claim.",
                    "Risk threshold for data_transfer_abroad not wired to auto-escalation.",
                ],
                "feedback": "Tighten reviewer prompt to mandate citations; wire escalation list.",
            }
        )
    return _json(
        {
            "passed": True,
            "score": 0.91,
            "issues": [],
            "feedback": "Sample NDA produced 3 well-cited findings; thresholds applied correctly.",
        }
    )


# --- Use-mode stubs -----------------------------------------------------------
#
# Loops 4 and 5 are the killer evidence of use-mode (PRD §7). The stubs below
# deterministically exercise both loops so the audit trail and committed sample
# logs contain visible rejection / critique moments:
#
#   Reviewer pass 1:   cites a hallucinated article  -> Verifier rejects (Loop 4)
#   Reviewer recite:   cites a real article in the   -> Verifier accepts
#                      candidates list returned by
#                      semantic_search
#   Drafter pass 1:    vague placeholder draft       -> Critic rejects  (Loop 5)
#   Drafter pass 2:    refined draft                 -> Critic accepts
#
# Real models follow the same prompts; stubs are only used when OFFLINE_MODE=true
# (or when no OPENAI_API_KEY is set).


def _reviewer_initial(ctx: dict[str, Any]) -> str:
    """Pass-1 review: emits findings shaped to the actual document content,
    with the FIRST high-risk cross-border finding deliberately citing a
    hallucinated article so Loop 4 demonstrably fires.
    """
    overrides = ctx.get("interpretation_overrides", []) or []
    has_pdpl_override = any(
        "45 of 2021" in (o.get("law", "") or "") for o in overrides
    )
    document = (ctx.get("document_text") or "").lower()

    findings: list[dict[str, Any]] = []

    # Cross-border transfer — high risk if the doc lacks a consent qualifier.
    if "outside the uae" in document or "cross-border" in document or "transfer" in document:
        has_consent_qualifier = (
            "prior written consent" in document
            or "explicit consent" in document
            or "personal data" in document and "consent" in document
        )
        if not has_consent_qualifier:
            findings.append(
                {
                    "clause": (
                        "Vendor may transfer Confidential Information to its banking "
                        "partners outside the UAE without further notice to the Discloser."
                    ),
                    "risk": "high",
                    "confidence": 0.93,
                    # HALLUCINATED — PDPL has no Article 99. Loop 4 triggers.
                    "citation": {"law": "Federal Decree-Law 45 of 2021", "article": "99"},
                    "rationale": (
                        "Cross-border personal-data transfer without explicit consent "
                        "re-confirmation violates the org's tuned PDPL stance"
                        + (" (override applied)." if has_pdpl_override else ".")
                    ),
                }
            )

    # Onward sublicensing — second high-risk finding specific to data-broker pattern.
    if "sublicense" in document or "onward" in document or "analytics partners" in document:
        findings.append(
            {
                "clause": (
                    "Recipient may sublicense the Data to its own analytics partners, "
                    "including outside the UAE, under back-to-back terms substantially "
                    "similar to this Agreement."
                ),
                "risk": "high",
                "confidence": 0.90,
                "citation": {"law": "Federal Decree-Law 45 of 2021", "article": "7"},
                "rationale": (
                    "Onward transfer to undisclosed third parties without controller "
                    "consent breaches PDPL data-subject control requirements."
                ),
            }
        )

    # Confidentiality expiry — medium risk only when expiry is at termination.
    if "expire upon termination" in document:
        findings.append(
            {
                "clause": (
                    "The obligation of confidentiality shall expire upon termination "
                    "of this Agreement."
                ),
                "risk": "medium",
                "confidence": 0.84,
                "citation": {"law": "Federal Law 18 of 1993", "article": "87"},
                "rationale": (
                    "Confidentiality of trade information should extend beyond "
                    "termination for a reasonable period under commercial custom."
                ),
            }
        )

    # Survival-period adequacy — medium risk if survival exists but is short (<5y).
    if "survive termination" in document and any(
        f"({n})" in document or f" {n} " in document for n in ["one", "two", "three"]
    ):
        findings.append(
            {
                "clause": (
                    "The obligation of confidentiality shall survive termination of "
                    "this Agreement for a period of three (3) years, save for trade "
                    "secrets, which shall remain confidential indefinitely."
                ),
                "risk": "medium",
                "confidence": 0.78,
                "citation": {"law": "Federal Law 18 of 1993", "article": "396"},
                "rationale": (
                    "Three-year survival is below the five-year tail recommended by "
                    "the org's tuned stance; trade-secret carve-out is acceptable."
                ),
            }
        )

    # Short termination notice — low risk informational flag.
    if "seven (7) days written notice" in document or "7 days written notice" in document:
        findings.append(
            {
                "clause": (
                    "Either party may terminate this Agreement upon seven (7) days "
                    "written notice for any reason."
                ),
                "risk": "low",
                "confidence": 0.71,
                "citation": {"law": "Federal Law 5 of 1985", "article": "246"},
                "rationale": (
                    "Short notice for termination at convenience should be exercised "
                    "in accordance with the good-faith principle of the Civil Code."
                ),
            }
        )

    # No-audit-access clause — medium risk in data-broker pattern.
    if "not obligated to provide audit" in document or "self-certify" in document:
        findings.append(
            {
                "clause": (
                    "Recipient is not obligated to provide audit access to the "
                    "Discloser; provided that Recipient shall annually self-certify "
                    "compliance with this Agreement."
                ),
                "risk": "medium",
                "confidence": 0.80,
                "citation": {"law": "Federal Law 18 of 1993", "article": "70"},
                "rationale": (
                    "Self-certification without audit rights frustrates the good-"
                    "faith verification expected of commercial counterparties."
                ),
            }
        )

    # Fallback: if the heuristics didn't match, emit a low-risk informational
    # finding so the response is always well-formed for arbitrary inputs.
    if not findings:
        findings.append(
            {
                "clause": "General confidentiality language reviewed.",
                "risk": "low",
                "confidence": 0.65,
                "citation": {"law": "Federal Law 5 of 1985", "article": "246"},
                "rationale": (
                    "No material risks identified against the configured tuned stance; "
                    "standard good-faith obligations apply."
                ),
            }
        )

    return _json({"findings": findings})


def _reviewer_recite(ctx: dict[str, Any]) -> str:
    """Loop-4 retry: prefer a candidate in the same statute as the rejection.

    For the canonical cross-border-consent finding (rejected as PDPL Art 99),
    we want the re-cite to land on the legally correct article (PDPL Art 7).
    Falls back to the verifier's first candidate when no same-statute match.
    """
    finding = dict(ctx.get("finding", {}))
    candidates = ctx.get("candidate_articles", []) or []
    rejected_law = (finding.get("citation", {}) or {}).get("law", "")

    same_statute = [c for c in candidates if c.get("law") == rejected_law]
    pick = None
    if same_statute:
        pick = same_statute[0]
    elif "45 of 2021" in rejected_law:
        # PDPL canonical fallback: Article 7 (consent).
        pick = {"law": "Federal Decree-Law 45 of 2021", "article": "7"}
    elif candidates:
        pick = candidates[0]
    else:
        pick = {
            "law": rejected_law or "Federal Decree-Law 45 of 2021",
            "article": "7",
        }

    finding["citation"] = {
        "law": pick.get("law"),
        "article": str(pick.get("article", "")),
    }
    finding["rationale"] = (
        finding.get("rationale", "")
        + " Re-cited after Citation Verifier rejection."
    )
    return _json(
        {
            "clause": finding.get("clause"),
            "risk": finding.get("risk"),
            "confidence": finding.get("confidence"),
            "citation": finding["citation"],
            "rationale": finding["rationale"],
        }
    )


def _drafter(ctx: dict[str, Any]) -> str:
    """Loop-5 driver: emit a vague draft on attempt 1, a tight one on attempt 2+."""
    attempt = int(ctx.get("attempt", 1))
    finding = ctx.get("finding", {})
    clause_kind = (finding.get("clause", "") or "").lower()

    if attempt == 1:
        return _json(
            {
                "draft_clause": (
                    "The parties shall handle Confidential Information responsibly "
                    "and in accordance with applicable law."
                ),
                "rationale": "Initial draft.",
            }
        )

    # Tight, statute-anchored revision.
    if "transfer" in clause_kind or "banking" in clause_kind:
        replacement = (
            "Any cross-border transfer of Confidential Information that includes "
            "Personal Data shall require the Discloser's prior explicit written "
            "consent re-confirmed at the time of transfer, with a documented "
            "audit log retained for the duration of the contract and seven (7) "
            "years thereafter, consistent with Federal Decree-Law 45 of 2021 "
            "(PDPL) consent requirements."
        )
    elif "confidentiality" in clause_kind and "expire" in clause_kind:
        replacement = (
            "Obligations of confidentiality shall survive termination of this "
            "Agreement for a period of five (5) years, save for trade secrets "
            "and Personal Data, which shall remain confidential indefinitely "
            "until they enter the public domain by lawful means."
        )
    else:
        replacement = (
            "Termination shall be exercised in good faith with not less than "
            "thirty (30) days' prior written notice for convenience, save for "
            "termination for cause for which the notice period shall be seven "
            "(7) days following a written cure period."
        )

    return _json(
        {
            "draft_clause": replacement,
            "rationale": "Revised to anchor on the cited statute and tighten scope.",
        }
    )


def _critic(ctx: dict[str, Any]) -> str:
    """Loop-5 critic: rejects the vague first draft, accepts the revision."""
    attempt = int(ctx.get("attempt", 1))
    draft = ctx.get("draft", {}) or {}
    text = (draft.get("draft_clause", "") or "").lower()

    too_vague = (
        attempt == 1
        or "responsibly" in text
        or "applicable law" in text and len(text) < 160
    )
    if too_vague:
        return _json(
            {
                "accepted": False,
                "critique": (
                    "Draft is too generic; it does not anchor on the cited "
                    "statute or quantify the remediation. Tighten with explicit "
                    "obligations and reference the cited article."
                ),
            }
        )
    return _json(
        {
            "accepted": True,
            "critique": "Draft anchors on the cited statute and resolves the flagged risk.",
        }
    )


def _synthesis(ctx: dict[str, Any]) -> str:
    risks = ctx.get("risk_buckets", {}) or {}
    findings_count = int(ctx.get("findings_count", 0))
    rejections = int(ctx.get("rejections", 0))
    critiques = int(ctx.get("critiques", 0))
    recommendation = (
        "DO NOT SIGN as-is — material PDPL risk on cross-border transfer."
        if risks.get("high", 0) > 0
        else "Acceptable subject to the listed counter-proposals."
    )
    return _json(
        {
            "summary": {
                "total_findings": findings_count,
                "high_risk": risks.get("high", 0),
                "medium_risk": risks.get("medium", 0),
                "low_risk": risks.get("low", 0),
                "verified_citations": findings_count,
                "citation_rejections": rejections,
                "draft_critiques": critiques,
                "recommendation": recommendation,
            }
        }
    )


_AGENTS = {
    "Interviewer": _interviewer,
    "Debater A": _debater_a,
    "Debater B": _debater_b,
    "Architect": _architect,
    "Builder": _builder,
    "Validator": _validator,
}


# The Reviewer agent name covers three distinct prompt contracts (initial review,
# re-cite after Loop 4, critic in Loop 5). We disambiguate on the system prompt.

def _route_reviewer(messages: list, ctx: dict[str, Any]) -> str:
    system_text = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_text = str(msg.get("content", ""))
            break
    sys_lower = system_text.lower()
    # The critic prompt opens with "You are the Reviewer acting as critic.".
    if "acting as critic" in sys_lower:
        return _critic(ctx)
    # The recite prompt is the only one that names the verifier rejection in
    # its opening line. Use the full phrase so the offline router doesn't
    # mis-route the initial review when its system prompt mentions "re-cite"
    # in passing (e.g. "Loop 4 will reject hallucinated citations and you'll
    # be asked to re-cite.").
    if "rejected your prior citation" in sys_lower:
        return _reviewer_recite(ctx)
    return _reviewer_initial(ctx)


_USE_AGENT_GENERATORS = {
    "Reviewer": _route_reviewer,
    "Counter-Proposal Drafter": lambda messages, ctx: _drafter(ctx),
    "Citation Verifier": lambda messages, ctx: _json(
        {"note": "Citation Verifier is orchestrator-driven; no LLM call expected.", "ok": True}
    ),
    "Synthesis": lambda messages, ctx: _synthesis(ctx),
}


def stub_chat(agent_name: str, messages: list) -> str:
    ctx = _extract_ctx(messages)
    use_gen = _USE_AGENT_GENERATORS.get(agent_name)
    if use_gen is not None:
        return use_gen(messages, ctx)
    generator = _AGENTS.get(agent_name)
    if generator is None:
        return _json({"note": f"offline stub for {agent_name}", "ok": True})
    return generator(ctx)


def stub_embed(text: str) -> list[float]:
    """Deterministic pseudo-embedding from a hash, normalized to unit length.

    Good enough for offline retrieval smoke tests; real embeddings come from the
    configured model when OFFLINE_MODE is off.
    """
    digest = hashlib.sha256((text or "").encode("utf-8")).digest()
    raw = [b - 128 for b in digest]
    vec = [raw[i % len(raw)] * (1 + (i // len(raw))) for i in range(_EMBED_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
