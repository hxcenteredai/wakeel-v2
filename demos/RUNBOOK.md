# `demos/` — Technical walkthrough recording runbook

Three short MP4s required by Milestone 2 Amendment §3 criterion 6 and PRD §17:

| # | File                          | Scenario                                                                                            | Length |
|---|-------------------------------|-----------------------------------------------------------------------------------------------------|--------|
| 1 | `01_build_mode_walkthrough.mp4` | Build mode: factory mints a copilot end-to-end, with Loops 1-3 visible in the audit-trail sidebar.   | 60–90s |
| 2 | `02_use_mode_walkthrough.mp4`   | **The killer moment.** Use mode against an aggressive NDA — must capture the Citation Verifier rejection (Loop 4) as it rolls in the audit trail.   | 60–90s |
| 3 | `03_arabic_intake_walkthrough.mp4` | Arabic intake: the Interviewer accepts the hospital-themed Arabic NDA and replies in Arabic.        | 60–90s |

The MP4s in this folder are the **delivered raw recordings** per SOW §2 —
1080p, no editing, with red-box / caption overlays burned in. The shot lists
below describe what each video must capture if it ever needs to be re-shot.

The Playwright-based recorder used to produce these MP4s is kept locally
(not committed) — the deliverable is the MP4 itself, not the tooling that
generated it.

---

## Common setup (run once)

```bash
# Two terminals, one repo.
git checkout feat/m2-use-mode-delivery
cp .env.example .env
# In .env, set:
#   OPENAI_BASE_URL=https://api.core42.ai/v1   (or your dev OpenAI key)
#   OPENAI_API_KEY=<key>
#   OFFLINE_MODE=false                              (or true for stub demo)

python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.corpus.ingest        # populates 4 UAE statutes
```

Then in two terminals:

```bash
# T1
python run.py                       # API on :8000

# T2
python run_ui.py                    # Streamlit UI on :8001
```

Open `http://localhost:8001` in a browser at 1920×1080 with the audit-trail
sidebar pinned open. Use a screen-recorder (QuickTime "New Screen Recording",
OBS, or Loom — 1080p, MP4). No editing required — raw recordings are
acceptable per SOW §2.

---

## 1. `01_build_mode_walkthrough.mp4` (60–90s)

**Shot list:**

1. **0:00–0:05** — UI loaded on Build mode. Audit sidebar visible (empty).
2. **0:05–0:15** — Paste Sarah's intake (`input_examples/build_mode/01_english_nda_fintech.json` → `intake.workflow_description`) into the chat input. Submit.
3. **0:15–0:55** — As the run progresses, narrate (or annotate on-screen) the audit entries as they appear: Interviewer → Debater A → Debater B → Architect (request_more_debate, Loop 1) → second debate round → Architect (synthesize) → Builder → Validator (rejected, Loop 2) → Builder (revised) → Validator (passed).
4. **0:55–end** — Final response: `copilot_id` and the config summary visible in chat. Audit trail shows the full Loop 1 + Loop 2 firing pattern.

**Must capture:** the moment Loop 1 fires (extra debate round) and the moment Loop 2 fires (Validator rejects the first build).

---

## 2. `02_use_mode_walkthrough.mp4` (60–90s) — **the killer demo**

**Shot list:**

1. **0:00–0:05** — UI switched to Use mode. Pick the `cp_*` minted in demo #1 from the copilot dropdown.
2. **0:05–0:15** — Paste the body of `input_examples/use_mode/01_aggressive_vendor_nda.json` into the document input. Submit.
3. **0:15–0:45** — Watch the audit sidebar. Pause briefly on:
   - **Reviewer → emits 3 findings**, first finding cites `Federal Decree-Law 45 of 2021 Article 99` (hallucinated).
   - **Citation Verifier → decision: rejected** with `Article not found in corpus` (this is the moment — pause 2–3 seconds here).
   - **Reviewer → re-cites** as `Article 7` (the real PDPL consent article).
   - **Citation Verifier → decision: verified** with the exact statute text appearing.
4. **0:45–end** — Counter-Proposal Drafter emits a vague first draft → Reviewer-as-critic rejects (Loop 5) → Drafter revises → Critic accepts. Final response shows 3 findings with "DO NOT SIGN" recommendation.

**Must capture:** the Citation Verifier rejection moment is the demo's main visual asset (PRD §16: "Pause on the rejection log. This is the moment.").

**Optional companion shot (B-roll, 5–10s) — decision history endpoint.** After
the use-mode run finishes, drop to a terminal and run:

```bash
curl -s http://localhost:8000/decisions/<cp_xxx> | jq '{
  copilot_id,
  total_runs,
  total_decisions,
  runs: [.runs[] | {run_id, mode, loops_fired, entry_count}]
}'
```

Show the structured response — every run this copilot ever made, including
the one that just finished, with `loops_fired` listing `Loop 4` and `Loop 5`.
This makes the "decisions are persistent and queryable" story explicit:
every decision the copilot makes is auditable via a single HTTP call, no
filesystem access needed.

---

## 3. `03_arabic_intake_walkthrough.mp4` (60–90s)

**Shot list:**

1. **0:00–0:05** — UI on Build mode, fresh session.
2. **0:05–0:20** — Paste the Arabic body from `input_examples/build_02_hospital_nda_ar.json` (`intake.workflow_description`) into the chat input. Show the RTL rendering (cursor on the right, text flows right-to-left).
3. **0:20–0:50** — Submit. Show the Interviewer's response coming back in Arabic. Audit sidebar shows `agent=Interviewer  action=extract_requirements language=ar`.
4. **0:50–end** — Either let the rest of the build play out (Loops 1-3 still fire), or stop at the Arabic intake confirmation — either is acceptable since the criterion targets *Arabic intake*, not full bilingual end-to-end.

**Must capture:** RTL rendering of the input, and the Interviewer's Arabic-language `response_to_user` value.

---

## Recording mechanics

- **Resolution:** 1920×1080 (the UI was tested at this size).
- **Frame rate:** 30 fps default of QuickTime / OBS is fine.
- **Audio:** voiceover optional. Silent recordings with on-screen text or subtitles are acceptable per SOW §2.
- **Output:** drop the MP4 file straight into `demos/` with the exact filename from the table above.
- **No editing required.** Don't trim, transition, or compress beyond the recorder's default codec (H.264).
- **Verify the file size:** each MP4 should be < 50 MB at 60-90s, 1080p, default codec. Larger than that, drop the bitrate.

When all three are recorded, commit:

```bash
git add demos/*.mp4
git commit -m "demos: M2 walkthrough videos (build, use mode citation rejection, Arabic intake)"
```

---

## Regenerating the videos (local dev tooling)

A Playwright-based recorder (`scripts/demos/record_walkthroughs.py`) drives
the live UI through each shot list and emits annotated MP4s end-to-end.
It is **not committed** — re-pull it from a developer's local workspace or
re-author it if a regeneration is needed. The committed deliverable is the
three MP4 files themselves; the SOW §2 acceptance criterion targets the
recordings, not the tooling that produced them.
