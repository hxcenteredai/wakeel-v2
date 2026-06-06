"""Streamlit chat UI (port 8001).

Run:  streamlit run run_ui.py --server.port 8001
  or: python run_ui.py   (delegates to the streamlit CLI)

Layout (PRD section 10):
  - Top: mode toggle (Build / Use)
  - Center: chat interface (st.chat_message / st.chat_input)
  - Right: audit-trail panel (per-step expanders) + config/copilot preview
  - Arabic input is detected and rendered right-to-left.
"""
from __future__ import annotations

import os
import re
import sys

import requests
import streamlit as st

from app.audit_humanizer import humanize as _humanize_entry

BACKEND_URL = os.environ.get("WAKEEL_BACKEND_URL", "http://localhost:8000")
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _looks_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def _call_backend(payload: dict) -> dict:
    resp = requests.post(f"{BACKEND_URL}/run", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()


def _get_config() -> dict:
    try:
        return requests.get(f"{BACKEND_URL}/config", timeout=15).json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _list_copilots() -> list[dict]:
    """GET /copilots — list copilots registered server-side (persists across UI restarts)."""
    try:
        resp = requests.get(f"{BACKEND_URL}/copilots", timeout=15)
        resp.raise_for_status()
        return resp.json().get("copilots", [])
    except Exception:  # noqa: BLE001
        return []


def _risk_badge(risk: str) -> str:
    """Inline coloured risk pill (high/medium/low)."""
    color = {"high": "#c0392b", "medium": "#d68910", "low": "#1e8449"}.get(risk, "#7f8c8d")
    return (
        f"<span style='background:{color};color:white;padding:2px 8px;"
        f"border-radius:10px;font-size:0.75em;font-weight:600;text-transform:uppercase'>"
        f"{risk}</span>"
    )


def _render_finding(idx: int, finding: dict) -> None:
    """Render a single Reviewer finding with verified citation + counter-proposal."""
    risk = finding.get("risk", "low")
    citation = finding.get("citation", {})
    verified = citation.get("verified", False)
    attempts = citation.get("verification_attempts", 1)
    verify_pill = (
        "<span style='background:#1e8449;color:white;padding:2px 6px;"
        "border-radius:6px;font-size:0.7em'>VERIFIED</span>"
        if verified
        else "<span style='background:#c0392b;color:white;padding:2px 6px;"
        "border-radius:6px;font-size:0.7em'>UNVERIFIED</span>"
    )
    attempt_note = (
        f" <span style='color:#7f8c8d;font-size:0.8em'>(verified on attempt {attempts})</span>"
        if attempts > 1
        else ""
    )

    st.markdown(
        f"### Finding {idx}  {_risk_badge(risk)}",
        unsafe_allow_html=True,
    )
    st.markdown(f"**Clause:** _{finding.get('clause', '')}_")
    st.markdown(
        f"**Citation:** `{citation.get('law', '')}`, Article "
        f"`{citation.get('article', '')}` {verify_pill}{attempt_note}",
        unsafe_allow_html=True,
    )
    if citation.get("exact_text"):
        with st.expander("Exact text from the corpus"):
            st.code(citation["exact_text"], language="text")
    st.markdown(f"**Rationale:** {finding.get('rationale', '')}")
    if finding.get("counter_proposal"):
        iters = finding.get("counter_proposal_iterations", 0)
        iter_note = f" _(refined over {iters} Loop-5 critique cycles)_" if iters > 1 else ""
        st.markdown(f"**Counter-proposal**{iter_note}:")
        st.info(finding["counter_proposal"])


USE_MODE_EXAMPLES = {
    "Aggressive vendor NDA (triggers Loops 4 + 5)": (
        "input_examples/use_mode/01_aggressive_vendor_nda.json"
    ),
    "Balanced commercial partner NDA": (
        "input_examples/use_mode/02_balanced_partner_nda.json"
    ),
    "Data-broker NDA (high-risk)": (
        "input_examples/use_mode/03_data_broker_nda.json"
    ),
}


def _load_use_example(label: str) -> tuple[str, str]:
    """Load a committed use-mode example file → (title, content)."""
    import json as _json
    from pathlib import Path as _Path

    path = _Path(__file__).parent / USE_MODE_EXAMPLES[label]
    with open(path, "r", encoding="utf-8") as fh:
        payload = _json.load(fh)
    doc = payload.get("document", {})
    return doc.get("title", ""), doc.get("content", "")


def _render_settings_sidebar() -> None:
    """Left sidebar: change the LLM API key / endpoint / models at runtime."""
    with st.sidebar:
        st.header("API settings")
        cfg = _get_config()
        if "error" in cfg:
            st.error(f"Backend unreachable: {cfg['error']}")
        else:
            mode = "OFFLINE (stub)" if cfg.get("offline_mode") else "LIVE"
            st.caption(f"Mode: **{mode}**  ·  key: `{cfg.get('api_key_masked') or 'none'}`")

        with st.form("llm_config"):
            st.caption("Swap provider without restarting. Leave blank to keep current.")
            api_key = st.text_input("API key", type="password", placeholder="sk-... / leave blank to keep")
            base_url = st.text_input("Base URL", value=cfg.get("base_url") or "", placeholder="https://api.openai.com/v1")
            models = cfg.get("models", {}) if isinstance(cfg, dict) else {}
            default_model = st.text_input("Standard model", value=models.get("standard", ""))
            reasoning_model = st.text_input("Reasoning model", value=models.get("reasoning", ""))
            embedding_model = st.text_input("Embedding model", value=models.get("embedding", ""))
            submitted = st.form_submit_button("Apply")

        if submitted:
            payload = {
                "base_url": base_url or None,
                "default_model": default_model or None,
                "reasoning_model": reasoning_model or None,
                "embedding_model": embedding_model or None,
            }
            if api_key:
                payload["api_key"] = api_key
            try:
                r = requests.post(f"{BACKEND_URL}/config", json=payload, timeout=30)
                r.raise_for_status()
                st.success("Applied. New config active.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed to apply: {exc}")


def _render_audit(container, audit_trail: list[dict]) -> None:
    container.subheader("Audit trail")
    if not audit_trail:
        container.caption("No steps yet. Submit a request to see the agent council run.")
        return
    loops_seen = sorted({e["loop"] for e in audit_trail if e.get("loop")})
    if loops_seen:
        container.success("Loops fired: " + ", ".join(loops_seen))
    for i, entry in enumerate(audit_trail, 1):
        # Business-readable headline (see app/audit_humanizer.py).
        label = f"{i}. {_humanize_entry(entry)}"
        with container.expander(label, expanded=False):
            st.write(f"**Decision:** {entry.get('decision') or '—'}")
            st.write(f"**Reason:** {entry.get('reason') or '—'}")
            if entry.get("details"):
                st.json(entry["details"])
            # Technical breadcrumb for engineers / judges cross-referencing logs.
            st.caption(
                f"`agent={entry.get('agent', '')}  action={entry.get('action', '')}  "
                f"loop={entry.get('loop') or '-'}  decision={entry.get('decision') or '-'}`"
            )


def _build_mode(chat_col, side_col) -> None:
    with chat_col:
        st.caption("Describe the regulatory workflow you want a copilot for (English or Arabic).")
        for msg in st.session_state.build_messages:
            with st.chat_message(msg["role"]):
                if msg.get("rtl"):
                    st.markdown(
                        f"<div dir='rtl' style='text-align:right'>{msg['content']}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(msg["content"])

    prompt = st.chat_input("e.g. Review vendor NDAs against our fintech data-handling stance")
    if prompt:
        rtl = _looks_arabic(prompt)
        st.session_state.build_messages.append({"role": "user", "content": prompt, "rtl": rtl})
        with chat_col:
            with st.chat_message("user"):
                if rtl:
                    st.markdown(
                        f"<div dir='rtl' style='text-align:right'>{prompt}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(prompt)

        payload = {
            "mode": "build",
            "intake": {
                "workflow_description": prompt,
                "language": "ar" if rtl else "en",
            },
        }
        with st.spinner("Agent council running…"):
            try:
                result = _call_backend(payload)
            except Exception as exc:  # noqa: BLE001
                with chat_col:
                    st.error(f"Backend error: {exc}")
                return

        st.session_state.build_audit = result.get("audit_trail", [])
        st.session_state.last_copilot_id = result.get("copilot_id")
        st.session_state.last_config = result.get("config", {})

        interviewer_response = result.get("interviewer_response", "")
        summary = (
            f"**Copilot built:** `{result.get('copilot_id')}`\n\n"
            f"**Validation:** {result.get('validation_results', {}).get('score', '—')} "
            f"(passed={result.get('validation_results', {}).get('passed')})"
        )
        if interviewer_response:
            st.session_state.build_messages.append(
                {"role": "assistant", "content": interviewer_response, "rtl": _looks_arabic(interviewer_response)}
            )
        st.session_state.build_messages.append({"role": "assistant", "content": summary})
        st.rerun()

    with side_col:
        _render_audit(st, st.session_state.build_audit)
        if st.session_state.get("last_copilot_id"):
            st.subheader("Copilot config")
            st.code(st.session_state.last_copilot_id)
            # PRD §10: "Try it now" button switches to Use mode pre-populated.
            # Streamlit forbids writing to a widget's session_state key after the
            # widget is instantiated, so we stash the request in *pending_* keys
            # and apply them at the top of main() on the next rerun, before the
            # mode_toggle radio and use_copilot_select selectbox are rendered.
            if st.button("Try it now", type="primary", key="try_it_now_btn"):
                st.session_state.pending_mode_switch = "Use"
                st.session_state.pending_copilot_select = st.session_state.last_copilot_id
                st.rerun()
            st.json(st.session_state.last_config)


def _use_mode(chat_col, side_col) -> None:
    """Use mode (M2): pick a copilot, paste a document, get findings + verified citations."""
    if "use_audit" not in st.session_state:
        st.session_state.use_audit = []
    if "use_response" not in st.session_state:
        st.session_state.use_response = None
    if "use_doc_title" not in st.session_state:
        st.session_state.use_doc_title = ""
    if "use_doc_content" not in st.session_state:
        st.session_state.use_doc_content = ""

    with chat_col:
        st.caption(
            "Select a copilot you've built, paste a document, and the Reviewer "
            "council will review it against the local statute corpus — every "
            "citation is verified against exact corpus text (Loop 4) and every "
            "counter-proposal is critiqued back to the Drafter until accepted (Loop 5)."
        )

        registered = _list_copilots()
        local_id = st.session_state.get("last_copilot_id")
        ids = [c["copilot_id"] for c in registered]
        if local_id and local_id not in ids:
            ids = [local_id] + ids
        if not ids:
            st.warning(
                "No copilots registered yet. Switch to **Build** mode first and "
                "describe an NDA-review workflow to mint one."
            )
            st.selectbox("Select a copilot_id", options=["(none built yet)"], disabled=True)
            return

        selected = st.selectbox("Select a copilot_id", options=ids, key="use_copilot_select")

        example_col, _ = st.columns([3, 1])
        with example_col:
            example_label = st.selectbox(
                "Load a committed example (optional)",
                options=["—"] + list(USE_MODE_EXAMPLES.keys()),
                key="use_example_select",
            )
            if st.button("Load example", disabled=(example_label == "—"), key="use_load_btn"):
                title, content = _load_use_example(example_label)
                st.session_state.use_doc_title = title
                st.session_state.use_doc_content = content
                st.rerun()

        st.text_input(
            "Document title",
            key="use_doc_title",
            placeholder="e.g. Vendor Master NDA — v1",
        )

        # SOW §2 / PRD §10: "text paste or file upload for document"
        uploaded = st.file_uploader(
            "Upload a document (optional)",
            type=["txt", "md"],
            key="use_doc_upload",
            help="Plain text or markdown. Paste below if you prefer.",
        )
        if uploaded is not None:
            try:
                content = uploaded.read().decode("utf-8", errors="replace")
            except Exception:
                content = ""
            if content and content != st.session_state.use_doc_content:
                st.session_state.use_doc_content = content
                if not st.session_state.use_doc_title:
                    st.session_state.use_doc_title = uploaded.name
                st.rerun()

        st.text_area(
            "Document text",
            key="use_doc_content",
            height=220,
            placeholder="Paste the NDA (or other document) body here…",
        )

        submit = st.button(
            "Review document",
            type="primary",
            disabled=not st.session_state.use_doc_content.strip(),
            key="use_submit_btn",
        )

        if submit:
            payload = {
                "mode": "use",
                "copilot_id": selected,
                "document": {
                    "type": "text",
                    "content": st.session_state.use_doc_content,
                    "title": st.session_state.use_doc_title or None,
                },
            }
            with st.spinner("Reviewer + Citation Verifier + Drafter running…"):
                try:
                    result = _call_backend(payload)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Backend error: {exc}")
                    return
            st.session_state.use_response = result
            st.session_state.use_audit = result.get("audit_trail", [])
            st.rerun()

        response = st.session_state.use_response
        if response:
            st.divider()
            summary = response.get("summary", {})
            rec = summary.get("recommendation", "")
            rec_color = "#c0392b" if rec.upper().startswith("DO NOT SIGN") else "#1e8449"
            st.markdown(
                f"<div style='background:{rec_color};color:white;padding:10px 14px;"
                f"border-radius:8px;font-weight:600'>Recommendation: {rec}</div>",
                unsafe_allow_html=True,
            )
            cols = st.columns(5)
            cols[0].metric("Findings", summary.get("total_findings", 0))
            cols[1].metric("High", summary.get("high_risk", 0))
            cols[2].metric("Medium", summary.get("medium_risk", 0))
            cols[3].metric("L4 rejections", summary.get("citation_rejections", 0))
            cols[4].metric("L5 critiques", summary.get("draft_critiques", 0))

            st.subheader("Findings")
            findings = response.get("findings", [])
            if not findings:
                st.caption("Reviewer surfaced no risks against the configured stance.")
            for i, finding in enumerate(findings, 1):
                with st.container(border=True):
                    _render_finding(i, finding)

    with side_col:
        _render_audit(st, st.session_state.use_audit)


def main() -> None:
    st.set_page_config(page_title="Wakeel", layout="wide")
    st.title("Wakeel — Regulatory Agent Factory")

    _render_settings_sidebar()

    if "build_messages" not in st.session_state:
        st.session_state.build_messages = []
    if "build_audit" not in st.session_state:
        st.session_state.build_audit = []

    # Apply any pending switches from a previous "Try it now" click. We manage
    # the active mode in our own session_state slot (``active_mode``) and feed
    # it to the radio via ``index=…`` rather than ``key=``. Reason: when a radio
    # has a ``key`` and we assign ``st.session_state[key] = "Use"`` before it
    # renders, Streamlit honours the new value for the *return* but does not
    # always update the *visual* radio dot — the radio ends up showing "Build"
    # while the page renders Use-mode content, which is confusing for users.
    # The selectbox in ``_use_mode`` (``use_copilot_select``) does not suffer
    # from this quirk and is still pre-populated via session_state.
    if "active_mode" not in st.session_state:
        st.session_state.active_mode = "Build"
    pending_mode = st.session_state.pop("pending_mode_switch", None)
    if pending_mode in ("Build", "Use"):
        st.session_state.active_mode = pending_mode
    pending_copilot = st.session_state.pop("pending_copilot_select", None)
    if pending_copilot:
        st.session_state.use_copilot_select = pending_copilot

    _mode_options = ["Build", "Use"]
    mode = st.radio(
        "Mode",
        _mode_options,
        horizontal=True,
        index=_mode_options.index(st.session_state.active_mode),
    )
    st.session_state.active_mode = mode
    st.divider()

    chat_col, side_col = st.columns([2, 1], gap="large")
    if mode == "Build":
        _build_mode(chat_col, side_col)
    else:
        _use_mode(chat_col, side_col)


def _running_under_streamlit() -> bool:
    """True when this module is executing inside a Streamlit script runtime."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


if _running_under_streamlit():
    # Launched via `streamlit run run_ui.py` (or the Docker entrypoint).
    main()
elif __name__ == "__main__":
    # Launched via `python run_ui.py` — delegate to the streamlit CLI on :8001.
    import subprocess

    sys.exit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                os.path.abspath(__file__),
                "--server.port",
                "8001",
                "--server.address",
                "0.0.0.0",
            ]
        )
    )
