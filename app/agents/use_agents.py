"""Use-mode agents (PRD section 7).

Reviewer (reasoning) -> Citation Verifier (standard, Loop 4) ->
Counter-Proposal Drafter (standard) <-> Reviewer-as-critic (Loop 5) ->
Synthesis (standard).

Each function calls the shared LLM wrapper via app.agents.base.call_llm and
records its action on the run's AuditTrail. Loop control (4, 5) lives in
app.graph.use_graph; these functions are the per-agent units of work.
"""
from __future__ import annotations

from typing import Any

from app.agents.base import as_dict, as_list, call_llm
from app.corpus import retrieval
from app.logging_utils import AuditTrail


# Document clip ceiling for the Reviewer prompt. Real-world NDAs are routinely
# 8-15 KB; the original 4 KB clip silently dropped the indemnity / choice-of-law
# / survival clauses that usually sit at the END of the document — exactly the
# clauses the Reviewer most needs to flag. 16 KB (~4 K tokens) sits well under
# every Compass model's input limit and Compass uses group-level quotas with no
# per-request cap, so widening this is safe.
_DOCUMENT_CLIP_CHARS = 16000


# --- System prompts -----------------------------------------------------------

REVIEWER_SYS = (
    "You are the Reviewer agent of an NDA-review copilot configured for the "
    "client's organisation. Your job is to flag every clause that conflicts "
    "with the configured interpretation overrides, risk thresholds, or UAE "
    "regulatory baselines (PDPL, Labour Law, Commercial Transactions Law, "
    "Civil Transactions Law).\n"
    "\n"
    "CHECKLIST — flag a clause when ANY of these apply:\n"
    "  - Cross-border personal-data transfer without explicit consent, notice, "
    "or an adequacy/contractual safeguard (PDPL Art. 22).\n"
    "  - Confidentiality obligations that expire on termination rather than "
    "surviving for a defined period (Commercial Transactions baseline).\n"
    "  - Termination-for-convenience on short notice (<30 days) without cause "
    "where data-handling duties continue.\n"
    "  - Liability caps that are unreasonably low relative to the data risk, "
    "or that exclude breach of confidentiality / PDPL violations.\n"
    "  - Broad permitted-disclosure carve-outs (banking partners, affiliates, "
    "subcontractors) without flow-down obligations or onward-transfer limits.\n"
    "  - Any clause that conflicts with the org-tuned interpretation "
    "overrides supplied in the request context.\n"
    "\n"
    "OUTPUT — return STRICT JSON in this exact shape:\n"
    "{\n"
    '  \"findings\": [\n'
    "    {\n"
    '      \"clause\": \"<short identifier or first 80 chars of the clause>\",\n'
    '      \"risk\": \"high\" | \"medium\" | \"low\",\n'
    '      \"confidence\": <number between 0 and 1>,\n'
    '      \"citation\": {\"law\": \"<full or short law name>\", \"article\": \"<article number>\"},\n'
    '      \"rationale\": \"<one sentence explaining the risk and the legal basis>\"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "RULES:\n"
    "  - `findings` MUST be a JSON array (use [] only if the document is "
    "genuinely clean — for vendor / data-broker NDAs this is rare).\n"
    "  - `citation` MUST be a JSON object with both `law` and `article` keys; "
    "do NOT return citation as a bare number or string.\n"
    "  - Cite ONLY articles you are confident exist in the configured corpus; "
    "Loop 4 will reject hallucinated citations and you'll be asked to re-cite.\n"
    "  - Respond with the JSON object only — no prose, no markdown fences."
)

REVIEWER_RECITE_SYS = (
    "You are the Reviewer. The Citation Verifier rejected your prior citation "
    "as not present in the corpus. Re-cite the same finding using ONE of the "
    "candidate articles supplied — pick the article whose subject matter most "
    "directly supports the rationale.\n"
    "\n"
    "Return STRICT JSON in this exact shape:\n"
    "{\n"
    '  \"clause\": \"<unchanged from the rejected finding>\",\n'
    '  \"risk\": \"high\" | \"medium\" | \"low\",\n'
    '  \"confidence\": <number between 0 and 1>,\n'
    '  \"citation\": {\"law\": \"<from the candidates list>\", \"article\": \"<from the candidates list>\"},\n'
    '  \"rationale\": \"<refined to match the new citation>\"\n'
    "}\n"
    "\n"
    "`citation` MUST be a JSON object with both `law` and `article` keys. Do "
    "NOT return citation as a bare number or string. Respond with the JSON "
    "object only — no prose, no markdown fences."
)

VERIFIER_SYS = (
    "You are the Citation Verifier. Given a (law, article) reference, you do "
    "NOT generate text — you only confirm whether the article exists in the "
    "ingested corpus. The orchestrator performs the corpus lookup; you record "
    "the decision."
)

DRAFTER_SYS = (
    "You are the Counter-Proposal Drafter. Given a flagged NDA clause and the "
    "Reviewer's rationale, draft a replacement clause that resolves the risk "
    "while remaining commercially acceptable. Return STRICT JSON: "
    "{draft_clause, rationale}."
)

CRITIC_SYS = (
    "You are the Reviewer acting as critic. Evaluate the Drafter's replacement "
    "clause against the same statute that triggered the original flag. Return "
    "STRICT JSON: {accepted: bool, critique: str}. Reject if the draft does "
    "not actually resolve the cited risk or is too vague."
)

SYNTHESIS_SYS = (
    "You are the Synthesis agent. Assemble the verified findings, counter-"
    "proposals, and audit trail into the final response summary. Return STRICT "
    "JSON: {summary: {total_findings, high_risk, medium_risk, low_risk, "
    "verified_citations, citation_rejections, draft_critiques, recommendation}}."
)


# --- Agent functions ----------------------------------------------------------

def reviewer(
    audit: AuditTrail,
    copilot_config: dict[str, Any],
    document: dict[str, Any],
    attempt: int = 1,
) -> dict[str, Any]:
    """Initial pass: produce findings with citations."""
    overrides = copilot_config.get("interpretation_overrides", []) or []
    thresholds = copilot_config.get("risk_thresholds", {}) or {}
    ctx = {
        "copilot_id": copilot_config.get("copilot_id"),
        "interpretation_overrides": overrides,
        "risk_thresholds": thresholds,
        "retrieval_rules": copilot_config.get("retrieval_rules", {}),
        "document_text": (document.get("content") or "")[:_DOCUMENT_CLIP_CHARS],
        "attempt": attempt,
    }
    # Surface the org stance and the document as plain-text directives so live
    # GPT-5.1 treats them as instructions rather than as opaque context blob.
    user_msg = _build_reviewer_user_message(overrides, thresholds, document)
    result, _ = call_llm(
        agent_name="Reviewer",
        tier="reasoning",
        system=REVIEWER_SYS,
        user=user_msg,
        ctx=ctx,
        expect_keys=["findings"],
    )
    findings = [as_dict(f) for f in as_list(result.get("findings"))]
    # Re-shape the result so downstream code (and the API response) only ever
    # sees a list of dicts under "findings".
    result = {**result, "findings": findings}
    audit.add(
        agent="Reviewer",
        action="review_document",
        decision=f"{len(findings)} findings",
        reason="Initial pass over the NDA.",
        details={"attempt": attempt, "findings_count": len(findings)},
    )
    return result


def _build_reviewer_user_message(
    overrides: list, thresholds: dict, document: dict[str, Any]
) -> str:
    """Format the org stance + document as a directive user message.

    The stance was previously buried inside the ``@@CTX@@`` JSON blob, which
    live models tend to treat as inert context. Moving it into the user prompt
    as plain text dramatically improves GPT-5.1 adherence to the configured
    org-tuned interpretation overrides.
    """
    lines = ["Review the NDA document below and emit findings with citations to the configured UAE statutes."]
    if overrides:
        lines.append("\nORG-TUNED INTERPRETATION OVERRIDES (apply these strictly):")
        for o in overrides:
            if isinstance(o, dict):
                topic = o.get("topic") or o.get("name") or o.get("id") or ""
                stance = o.get("stance") or o.get("rule") or o.get("description") or ""
                lines.append(f"  - {topic}: {stance}" if topic else f"  - {stance}")
            else:
                lines.append(f"  - {o}")
    if thresholds:
        lines.append("\nRISK THRESHOLDS:")
        for k, v in thresholds.items():
            lines.append(f"  - {k}: {v}")
    title = document.get("title") or "NDA"
    body = (document.get("content") or "")[:_DOCUMENT_CLIP_CHARS]
    lines.append(f"\nDOCUMENT — {title}:\n---\n{body}\n---")
    return "\n".join(lines)


def reviewer_recite(
    audit: AuditTrail,
    finding: dict[str, Any],
    verifier_feedback: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """Loop 4 retry: re-cite a single finding after a verifier rejection."""
    candidates = verifier_feedback.get("candidates", []) or []
    ctx = {
        "finding": finding,
        "rejected_citation": finding.get("citation", {}),
        "candidate_articles": candidates,
        "attempt": attempt,
    }
    user_msg = _build_recite_user_message(finding, candidates)
    result, _ = call_llm(
        agent_name="Reviewer",
        tier="reasoning",
        system=REVIEWER_RECITE_SYS,
        user=user_msg,
        ctx=ctx,
        expect_keys=["citation"],
    )
    # Live models occasionally collapse the citation object to a bare value
    # (``"citation": 43`` or ``"citation": "Article 43"``). Coerce it back to a
    # well-formed object using the candidate as a hint when possible.
    new_citation = _coerce_citation(
        result.get("citation"),
        fallback_law=(candidates[0].get("law") if candidates else None),
        fallback_article=(candidates[0].get("article") if candidates else None),
    )
    audit.add(
        agent="Reviewer",
        action="re_cite",
        decision="recited",
        reason="Citation Verifier rejected the prior cite.",
        loop="Loop 4",
        details={
            "attempt": attempt,
            "rejected": finding.get("citation", {}),
            "proposed": new_citation,
        },
    )
    out = dict(finding)
    out["citation"] = new_citation
    out.setdefault("rationale", finding.get("rationale", ""))
    return out


def _build_recite_user_message(finding: dict[str, Any], candidates: list) -> str:
    """Plain-text user message for the re-cite call (mirrors reviewer())."""
    lines = [
        "The Citation Verifier rejected your previous citation.",
        "Re-cite the SAME finding using ONE of the candidate articles below.",
        f"\nFINDING:\n  clause:  {finding.get('clause', '')}",
        f"  risk:      {finding.get('risk', '')}",
        f"  rationale: {finding.get('rationale', '')}",
        f"  rejected:  {finding.get('citation', {})}",
        "\nCANDIDATE ARTICLES (pick one):",
    ]
    for c in candidates or []:
        law = c.get("law") or c.get("short_name") or ""
        article = c.get("article") or ""
        lines.append(f"  - law: {law}, article: {article}")
    if not candidates:
        lines.append("  (no candidates returned by retrieval — use your best matching real UAE article)")
    return "\n".join(lines)


def _coerce_citation(
    raw: Any,
    *,
    fallback_law: str | None = None,
    fallback_article: Any = None,
) -> dict[str, Any]:
    """Guarantee a ``{law, article}`` dict from any model citation shape.

    Live models on this prompt have been observed to return:
      - ``{"law": ..., "article": ...}``   (expected)
      - ``43`` or ``"43"`` (bare article)
      - ``"Article 43 PDPL"`` (free text)
      - ``None`` / missing
    We normalise all of those into a dict so downstream code can always
    subscript or mutate citation fields safely.
    """
    if isinstance(raw, dict):
        return {
            "law": raw.get("law") or raw.get("short_name") or fallback_law or "",
            "article": raw.get("article") or fallback_article or "",
        }
    if raw is None or raw == "":
        return {"law": fallback_law or "", "article": fallback_article or ""}
    if isinstance(raw, (int, float)):
        return {"law": fallback_law or "", "article": str(raw)}
    if isinstance(raw, str):
        # Try to pull "Law X Article Y" out of free text; otherwise treat the
        # whole string as the article and use the fallback law.
        return {"law": fallback_law or "", "article": raw.strip()}
    return {"law": fallback_law or "", "article": fallback_article or ""}


def citation_verifier(
    audit: AuditTrail, finding: dict[str, Any], attempt: int
) -> dict[str, Any]:
    """Look up the cited article in the corpus. Returns verifier verdict."""
    citation = _coerce_citation(finding.get("citation"))
    law = citation.get("law", "")
    article = str(citation.get("article", "")).strip()
    short_name = _resolve_short_name(law)

    hit = retrieval.get_article(short_name, _strip_article_prefix(article)) if short_name else None
    candidates: list[dict[str, Any]] = []
    if hit is None:
        # Provide alternatives via semantic search to enable Loop 4 re-cite.
        try:
            results = retrieval.semantic_search(
                finding.get("clause", "") + " " + finding.get("rationale", ""),
                top_k=3,
            )
        except Exception:
            results = []
        for r in results:
            meta = r.get("metadata", {})
            candidates.append(
                {
                    "law": meta.get("law_name"),
                    "article": meta.get("article_number"),
                    "short_name": meta.get("short_name"),
                }
            )

    verdict = {
        "verified": hit is not None,
        "exact_text": hit.get("text") if hit else None,
        "candidates": candidates,
        "attempt": attempt,
    }
    audit.add(
        agent="Citation Verifier",
        action="verify_citation",
        decision="verified" if verdict["verified"] else "rejected",
        reason=("Exact-text retrieval succeeded." if verdict["verified"]
                else f"Article not found in corpus; offered {len(candidates)} candidates."),
        loop="Loop 4",
        details={
            "law": law,
            "article": article,
            "attempt": attempt,
            "candidates": [f"{c.get('short_name')} Art {c.get('article')}" for c in candidates],
        },
    )
    return verdict


def counter_proposal_drafter(
    audit: AuditTrail,
    finding: dict[str, Any],
    attempt: int = 1,
    critique: str | None = None,
) -> dict[str, Any]:
    """Draft a replacement clause for a flagged finding."""
    ctx = {
        "finding": finding,
        "attempt": attempt,
        "critique": critique or "",
    }
    result, _ = call_llm(
        agent_name="Counter-Proposal Drafter",
        tier="standard",
        system=DRAFTER_SYS,
        user="Draft a replacement clause that resolves the cited risk.",
        ctx=ctx,
        expect_keys=["draft_clause"],
    )
    audit.add(
        agent="Counter-Proposal Drafter",
        action="draft_counter_proposal",
        decision="drafted",
        reason=("Initial draft." if attempt == 1
                else f"Revised after critique (attempt {attempt})."),
        loop="Loop 5" if attempt > 1 else None,
        details={"attempt": attempt, "clause": finding.get("clause", "")[:80]},
    )
    return result


def reviewer_critic(
    audit: AuditTrail,
    finding: dict[str, Any],
    draft: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """Loop 5: critique a drafter proposal against the original cited statute."""
    ctx = {"finding": finding, "draft": draft, "attempt": attempt}
    result, _ = call_llm(
        agent_name="Reviewer",
        tier="reasoning",
        system=CRITIC_SYS,
        user="Critique the draft against the cited statute.",
        ctx=ctx,
        expect_keys=["accepted"],
    )
    accepted = bool(result.get("accepted"))
    audit.add(
        agent="Reviewer",
        action="critique_draft",
        decision="accepted" if accepted else "rejected",
        reason=result.get("critique", ""),
        loop="Loop 5",
        details={"attempt": attempt, "accepted": accepted},
    )
    return result


def synthesis(
    audit: AuditTrail, findings: list[dict[str, Any]], rejections: int, critiques: int
) -> dict[str, Any]:
    """Final summary assembly.

    Always returns a dict that satisfies the API response contract — even if
    the live model returns a scalar or wraps the payload differently.
    """
    risk_buckets = {
        "high": sum(1 for f in findings if _risk_of(f) == "high"),
        "medium": sum(1 for f in findings if _risk_of(f) == "medium"),
        "low": sum(1 for f in findings if _risk_of(f) == "low"),
    }
    verified_count = sum(
        1 for f in findings
        if (as_dict(f.get("citation")).get("verified")) is True
    )
    ctx = {
        "findings_count": len(findings),
        "rejections": rejections,
        "critiques": critiques,
        "risk_buckets": risk_buckets,
    }
    result, _ = call_llm(
        agent_name="Synthesis",
        tier="standard",
        system=SYNTHESIS_SYS,
        user="Produce the final summary block for the use-mode response.",
        ctx=ctx,
        expect_keys=["summary"],
    )
    # `summary` MUST be a dict downstream — coerce any non-dict model output
    # (scalar, list, string) into a structured fallback rather than letting
    # ``body["summary"]["x"]`` blow up the API consumer.
    summary = as_dict(result.get("summary"))
    summary.setdefault("total_findings", len(findings))
    summary.setdefault("high_risk", risk_buckets["high"])
    summary.setdefault("medium_risk", risk_buckets["medium"])
    summary.setdefault("low_risk", risk_buckets["low"])
    summary.setdefault("verified_citations", verified_count)
    summary.setdefault("citation_rejections", rejections)
    summary.setdefault("draft_critiques", critiques)
    summary.setdefault(
        "recommendation",
        _default_recommendation(risk_buckets, len(findings)),
    )
    audit.add(
        agent="Synthesis",
        action="assemble_response",
        decision="synthesised",
        reason="Final response assembled.",
        details={
            "total_findings": summary["total_findings"],
            "citation_rejections": rejections,
            "draft_critiques": critiques,
        },
    )
    return summary


def _risk_of(finding: Any) -> str:
    """Tolerant accessor: returns lowercased risk or '' for malformed entries."""
    if not isinstance(finding, dict):
        return ""
    risk = finding.get("risk", "")
    if isinstance(risk, str):
        return risk.lower()
    return str(risk).lower()


def _default_recommendation(buckets: dict[str, int], total: int) -> str:
    """Deterministic fallback when the live model omits the recommendation."""
    if buckets.get("high", 0) > 0:
        return "DO NOT SIGN — high-risk clauses require renegotiation."
    if buckets.get("medium", 0) > 0:
        return "Negotiate — medium-risk clauses should be revised before signing."
    if total == 0:
        return "Acceptable — no flagged clauses against the configured stance."
    return "Acceptable with minor amendments."


# --- Helpers ------------------------------------------------------------------

_LAW_SHORT_NAMES = {
    "Federal Decree-Law 33 of 2021": "Labour Law",
    "Federal Decree-Law 45 of 2021": "PDPL",
    "Federal Law 18 of 1993": "Commercial Transactions Law",
    "Federal Law 5 of 1985": "Civil Transactions Law",
}


def _resolve_short_name(law: str) -> str:
    """Map a full or short law name to the corpus short_name used in IDs."""
    if not law:
        return ""
    law = law.strip()
    if law in _LAW_SHORT_NAMES:
        return _LAW_SHORT_NAMES[law]
    if law in _LAW_SHORT_NAMES.values():
        return law
    # Heuristic: accept "Labour Law", "PDPL", or any string containing them.
    for full, short in _LAW_SHORT_NAMES.items():
        if short.lower() in law.lower() or full.lower() in law.lower():
            return short
    return law


def _strip_article_prefix(article: str) -> str:
    """Accept '43', 'Article 43', '(43)', 'Art. 43' → '43'."""
    if not article:
        return ""
    s = str(article).strip()
    for prefix in ("article", "art.", "art", "("):
        if s.lower().startswith(prefix):
            s = s[len(prefix):].lstrip(" .").rstrip(")")
    s = s.strip("() ").strip()
    return s
