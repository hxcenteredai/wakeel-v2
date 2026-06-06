# Milestone 1 — Verification walkthrough (fresh clone)

Written walkthrough for verifying **Milestone 1 (Build Mode)** from a clean
checkout, including `e2e_acceptance.py` against **Compass** from a UAE laptop.
Target time: ~5 minutes plus model latency.

**No code changes** — swap `.env` only. Model IDs below are confirmed available on
Compass (`gpt-4.1`, `gpt-5.1`, `text-embedding-3-large`).

## 0. Prerequisites

- Python **3.11**
- Compass credentials (client-side only)

## 1. Clone and set up

```bash
git clone https://github.com/hxcenteredai/wakeel.git
cd wakeel
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure the endpoint

```bash
cp .env.example .env
```

`.env.example` already ships the Compass model tiers. For **Compass verification**,
set your key and base URL (everything else can stay as copied):

```env
OPENAI_BASE_URL=https://api.core42.ai/v1
OPENAI_API_KEY=<your Compass key — client-side only>
DEFAULT_MODEL=gpt-4.1
REASONING_MODEL=gpt-5.1
EMBEDDING_MODEL=text-embedding-3-large
OFFLINE_MODE=false
```

> **Developer note:** for OpenAI-direct dev work, use
> `OPENAI_BASE_URL=https://api.openai.com/v1` and your OpenAI key instead.
>
> **Optional sovereign-AI demo:** `INTERVIEWER_MODEL=G42-INCEPTION-GPT41-MSA`
> routes only the Interviewer to Compass's Inception MSA model. Per-agent env
> override — no code change.

## 3. Smoke test the LLM connection (Acceptance criterion #1)

```bash
python -m app.llm
```

Expect `RESULT: OK` with a printed response, `mode: LIVE`, and your masked key.

## 4. Ingest the corpus (criterion #2)

```bash
python -m app.corpus.ingest
```

Expect `16 articles` across Labour Law (33/2021) and PDPL (45/2021).

## 5. Run the acceptance report (criteria #2, #3, #5)

```bash
python e2e_acceptance.py
```

This runs the customer journeys (English, Arabic, vague) and prints a gate-by-gate
verdict. Expect **"ALL EVALUATED GATES PASS"** and exit code `0`.

- Gate 1: health + models reachable
- Gate 2: corpus retrieval returns the right articles
- Gate 3: `POST /run` build returns a valid response (incl. Arabic)
- Gate 5: Loops 1–3 observed (Loop 3 is model-dependent live; see note below)

> Note on Loop 3 (clarification): it fires deterministically in offline mode (a
> committed sample lives in `logs/samples/`). Live, it depends on the model
> detecting ambiguity; with `gpt-4.1`/`gpt-5.1` on Compass it should fire on the
> vague scenario. If you want a guaranteed demonstration, run `--offline`.

## 6. (Optional) Pytest suite

```bash
pytest tests/                      # offline, deterministic, fast
WAKEEL_E2E_LIVE=1 pytest tests/    # against your live Compass endpoint
```

## 7. (Optional) Exercise the UI (criterion #4)

```bash
python run.py        # API on :8000
python run_ui.py     # UI on :8001  (in a second terminal)
```

Open http://localhost:8001 — toggle Build/Use, submit an English or Arabic
description, and watch the audit-trail sidebar populate with the loop firings.
The left **"API settings"** panel lets you change the key/endpoint/models live.

## 8. Inspect the audit trail (criterion #5)

```bash
ls logs/run_*.jsonl              # per-run audit trails
cat logs/samples/build_mode_run_loops_1_2_3.jsonl   # canonical Loops 1-3 sample
cat logs/llm_calls.jsonl         # per-call LLM logs (SOW Section 6)
```

## Gate checklist

| Gate | How verified |
|---|---|
| 1 LLM → Compass | `python -m app.llm` → `RESULT: OK` |
| 2 Corpus + retrieval | `python -m app.corpus.ingest` + report Gate 2 |
| 3 `POST /run` build | report Gate 3 (English + Arabic) |
| 4 Streamlit UI :8001 | open the UI, submit, watch audit trail |
| 5 Loops 1–3 in logs | report Gate 5 + `logs/run_*.jsonl` |
| 6 GitHub repo current | this clone is the repo |
