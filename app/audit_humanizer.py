"""Map raw audit-trail entries to business-readable narrative lines.

The audit trail is the spine of every demo: customers (in-house counsel, GCs,
paralegals) watch it scroll while the multi-agent council deliberates. Raw
entries like ``agent=Interviewer action=extract_requirements`` read as code,
not workflow.

This module turns each entry into a single human-readable line with a status
icon at the front:

    ▶  run boundary (start / complete)
    ✓  success / accept / verify
    ⟳  loop firing — another iteration ahead
    ℹ  informational / neutral
    ⚠  warning / error

The UI keeps the raw decision / reason / details inside the expander body, so
engineers and judges retain full forensic detail. Unmapped (agent, action)
tuples fall back to a faithful raw render, so adding a new agent never crashes
the panel.
"""
from __future__ import annotations

from typing import Any

ICON_RUN = "▶"
ICON_OK = "✓"
ICON_LOOP = "⟳"
ICON_INFO = "ℹ"
ICON_WARN = "⚠"


def _score_text(details: dict[str, Any]) -> str:
    score = details.get("score")
    if isinstance(score, (int, float)):
        return f"score {float(score):.2f}"
    return "score —"


def humanize(entry: dict[str, Any]) -> str:
    """Return a business-readable one-line label for a single audit entry.

    The returned string is prefixed with a status icon and is safe to use as
    a Streamlit expander label (it is plain text / unicode, no Markdown).
    """
    agent = entry.get("agent", "") or ""
    action = entry.get("action", "") or ""
    decision = entry.get("decision", "") or ""
    loop = entry.get("loop") or None
    details = entry.get("details") or {}

    if agent == "orchestrator" and action == "run_start":
        if decision == "use":
            cp = details.get("copilot_id")
            base = f"{ICON_RUN} Use run started"
            return f"{base} — copilot {cp}" if cp else base
        return f"{ICON_RUN} Build run started"

    if agent == "orchestrator" and action == "run_complete":
        return f"{ICON_RUN} Run complete"

    if agent == "orchestrator" and action == "persist_copilot":
        if decision == "error":
            return f"{ICON_WARN} Copilot registry persistence failed"
        return f"{ICON_RUN} Copilot persisted to registry"

    if agent == "Interviewer" and action == "extract_requirements":
        if loop == "Loop 3":
            if decision == "structured":
                return f"{ICON_OK} Loop 3: Interview clarification round complete"
            return f"{ICON_INFO} Loop 3: Interview iterating — more clarification needed"
        if decision == "needs_clarification":
            return f"{ICON_INFO} Interviewer asked the user for clarification"
        return f"{ICON_OK} Interview captured — requirements extracted"

    if agent in ("Debater A", "Debater B") and action == "debate_argument":
        n_args = len(details.get("arguments") or [])
        suffix = f" — {n_args} arguments" if n_args else ""
        if decision == "strict_compliance":
            return f"{ICON_OK} Strict Compliance position drafted (Debater A){suffix}"
        if decision == "business_practicality":
            return f"{ICON_OK} Business Practical position drafted (Debater B){suffix}"
        return f"{ICON_OK} {agent} drafted argument{suffix}"

    if agent == "Architect" and action == "synthesize_or_escalate":
        if decision == "synthesize":
            return f"{ICON_OK} Synthesis complete — copilot configuration drafted"
        if decision == "request_more_debate":
            return f"{ICON_LOOP} Loop 1: extra debate round requested by Architect"
        if decision == "needs_clarification":
            return f"{ICON_LOOP} Loop 3: Architect requested interview clarification"

    if agent == "Builder" and action == "instantiate_copilot":
        cp = details.get("copilot_id")
        base = f"{ICON_OK} Builder assembled copilot configuration"
        return f"{base} ({cp})" if cp else base

    if agent == "Validator" and action == "validate_copilot":
        score_txt = _score_text(details)
        if decision == "passed":
            return f"{ICON_OK} Copilot validated ({score_txt}, passed)"
        iteration = details.get("iteration") or 1
        return (
            f"{ICON_LOOP} Loop 2: Validator rejected on attempt {iteration} "
            f"— Builder iterating ({score_txt})"
        )

    if agent == "Reviewer" and action == "review_document":
        n = details.get("findings_count")
        if n is None:
            try:
                n = int(decision.split()[0])
            except (ValueError, IndexError, AttributeError):
                n = None
        attempt = details.get("attempt") or 1
        attempt_suffix = f" (pass {attempt})" if isinstance(attempt, int) and attempt > 1 else ""
        if n == 0:
            return f"{ICON_OK} Reviewer found no risks against the configured stance{attempt_suffix}"
        if n is not None:
            plural = "s" if n != 1 else ""
            return f"{ICON_OK} Reviewer flagged {n} risk finding{plural}{attempt_suffix}"
        return f"{ICON_OK} Reviewer reviewed document{attempt_suffix}"

    if agent == "Reviewer" and action == "re_cite":
        attempt = details.get("attempt") or 1
        return f"{ICON_LOOP} Loop 4: Reviewer re-cited (attempt {attempt})"

    if agent == "Citation Verifier" and action == "verify_citation":
        law = (details.get("law") or "").strip()
        article = str(details.get("article") or "").strip()
        ref_bits = [b for b in [law, f"Article {article}" if article else ""] if b]
        ref = ", ".join(ref_bits) if decision == "verified" else " ".join(ref_bits)
        if decision == "verified":
            return f"{ICON_OK} Citation verified: {ref}" if ref else f"{ICON_OK} Citation verified"
        base = f"{ICON_LOOP} Loop 4: Citation rejected"
        return f"{base} ({ref} not found in corpus — Reviewer must re-cite)" if ref else base

    if agent == "Counter-Proposal Drafter" and action == "draft_counter_proposal":
        attempt = details.get("attempt") or 1
        clause = (details.get("clause") or "").strip()
        snippet = (clause[:60] + "…") if len(clause) > 60 else clause
        if isinstance(attempt, int) and attempt > 1:
            return f"{ICON_LOOP} Loop 5: Counter-proposal revised (attempt {attempt})"
        base = f"{ICON_OK} Counter-proposal drafted"
        return f"{base} — '{snippet}'" if snippet else base

    if agent == "Reviewer" and action == "critique_draft":
        attempt = details.get("attempt") or 1
        if decision == "accepted":
            return f"{ICON_OK} Critic accepted counter-proposal (attempt {attempt})"
        return f"{ICON_LOOP} Loop 5: Critic rejected draft (attempt {attempt}) — Drafter revising"

    if agent == "Synthesis" and action == "assemble_response":
        n = details.get("total_findings")
        rejections = details.get("citation_rejections") or 0
        critiques = details.get("draft_critiques") or 0
        loop_summary = []
        if rejections:
            loop_summary.append(f"Loop 4 rejections: {rejections}")
        if critiques:
            loop_summary.append(f"Loop 5 critiques: {critiques}")
        suffix = f" ({', '.join(loop_summary)})" if loop_summary else ""
        if n is not None:
            plural = "s" if n != 1 else ""
            return f"{ICON_OK} Synthesis complete — {n} finding{plural} finalized{suffix}"
        return f"{ICON_OK} Synthesis complete{suffix}"

    fallback = f"{agent} — {action}".strip(" —")
    if loop:
        fallback += f" [{loop}]"
    if decision:
        fallback += f" ({decision})"
    icon = ICON_LOOP if loop else (ICON_WARN if decision == "error" else ICON_INFO)
    return f"{icon} {fallback}" if fallback else f"{icon} (empty entry)"
