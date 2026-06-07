# Wakeel — Sovereign Regulatory Agent Factory

Wakeel turns an organization's interpretation of UAE regulation into deployable
AI copilots. **Build Mode** interviews a user (English or Arabic), debates the
regulatory stance with an agent council, and instantiates a configured copilot.
**Use Mode** runs that copilot over documents — verifying every citation
against the statute corpus and drafting counter-proposals for flagged clauses.

This repository delivers **Milestone 1 (Build mode end-to-end)** plus
**Milestone 2 (Use mode + delivery)** — see the [M2 evidence doc](docs/use-mode-evidence.md)
for the gate-by-gate audit. Technical walkthrough videos referenced by
Amendment §3 criterion 6 live in [`demos/`](demos/RUNBOOK.md).

---

> ⚖️ **Disclaimer — not legal advice.** Wakeel is a decision-support tool
> for qualified compliance and legal professionals. Its findings, citations,
> risk ratings, and counter-proposals are **informational only**, may contain
> errors, and must be reviewed by a licensed attorney before any reliance or
> action. Wakeel does not create an attorney–client relationship and is not a
> substitute for professional legal judgment. Every output ships with an audit
> trail and confidence scores precisely so a human reviewer stays in the loop.

## 1. Problem Statement

Compliance officers and in-house counsel across UAE enterprises review high
volumes of NDAs, vendor agreements, employment contracts, and partnership
documents against the UAE's growing regulatory framework — UAE Federal
Decree-Law 33 of 2021 (Labour Law), Federal Decree-Law 45 of 2021 (Personal
Data Protection Law), Federal Law 5 of 1985 (Civil Transactions Law), Federal
Law 18 of 1993 (Commercial Transactions Law), and many others.

A typical UAE fintech compliance officer reviews 15–30 vendor NDAs per week. A
UAE hospital compliance director handles 40–60 supplier and partnership
contracts per month. Each document takes 45–90 minutes of manual
clause-by-clause comparison. The work is **slow**, **inconsistent across
reviewers**, **difficult to scale**, **error-prone**, and **hard to audit**.

**The deeper problem.** Generic legal AI products (Harvey, Casetext, vLex)
review documents against generic legal knowledge. They do not capture how
**your specific organization** interprets each clause:

- That **your** organization treats a 30-day notice period as standard but
  rejects 60-day notices.
- That **your** hospital requires explicit consent language beyond PDPL's
  statutory baseline.
- That **your** fintech's risk threshold for vendor data access is stricter
  than industry norms.

These interpretation choices are made by senior counsel over years. They live
in email threads, Slack discussions, half-written policy memos, and the
institutional memory of long-tenured staff. Generic AI cannot capture this —
yet without this interpretation layer, AI legal review is at best 40% useful.

**Target users**

| Persona | Role | Volume | Primary need |
|---|---|---|---|
| Sarah | In-house counsel, UAE fintech | 20–30 NDAs/week | Verified citations + fast redline against org stance |
| Ahmed | Compliance director, UAE hospital | 40–60 contracts/month | Higher throughput; reserve senior attention for high-risk 10% |
| Mohammed | Partner, boutique UAE law firm | Multiple client engagements | Junior-associate leverage; firm-wide consistency |

**Why this matters (hard ROI)** — for a typical mid-market UAE enterprise with
50 contracts/month at 1 hour senior counsel review per contract at
AED 1,000/hour fully-loaded: **AED 50,000/month without Wakeel** →
**~AED 6,250/month with Wakeel** → **~AED 525,000/year savings** (~$143,000
USD) with a payback period of ~2 months.

**What Wakeel adds** that generic legal AI doesn't: Wakeel encodes
**organizational interpretation as a deployable artifact**. Each Wakeel
copilot is built from a structured interview with the customer's senior
counsel, debated through dual-stance agents (strict vs. practical),
synthesized into a config, and instantiated as a deployable copilot. This is
the architectural moat — not the model, not the corpus, not the prompts.

See [`docs/architecture.md` §1](docs/architecture.md#1-problem-statement) for
the full problem analysis.

---

## 2. Use Case ID

**Use Case ID: 21**

This project targets **G42 Agentathon Problem Statement #21 — Legal
Intelligence**. Matches `metadata.json` `use_case_id` field.

---

## 3. Solution Overview

Wakeel is a sovereign UAE regulatory AI platform with two integrated modes.
**Build Mode** mints a custom legal copilot for an organization through a
multi-agent interview-debate-synthesis pipeline that encodes the
organization's interpretation of UAE law. **Use Mode** runs that copilot
against real documents — flagging risks, verifying every citation against the
actual statute text, drafting counter-proposals, and producing a complete
audit trail. The system runs entirely on Compass (G42's sovereign UAE AI
infrastructure) and uses Inception's Arabic-tuned model for native Arabic
intake.

**The bimodal design.** Most legal AI is monolithic — one interface, one
workflow. Wakeel is deliberately bimodal because the moat (org-tuned
interpretation) and the daily value (document review) require different agent
topologies:

- **Build Mode** (6 agents, Loops 1–3) mints copilots that encode interpretation.
- **Use Mode** (4 agents, Loops 4–5) runs those copilots on documents.

The same orchestration framework, audit trail, LLM wrapper, and corpus serve
both. Only the agent council and acceptance criteria differ per mode.

**Input → output summary**

| Mode | Input | Output |
|---|---|---|
| Build | Free-text workflow description (Arabic or English) + org context | `copilot_id` referencing a configured Wakeel copilot + validation report + audit trail |
| Use | Reference to an existing `copilot_id` + a document to review | Findings list with verified citations, severity tags, counter-proposals, recommendation banner, and audit trail |

**The killer feature: Loop 4 (Citation Verification).** Generic legal AI
hallucinates citations regularly — confidently citing "PDPL Article 99" when
no such article exists. Wakeel's Citation Verifier is **not an LLM call** — it
is orchestrator-driven exact-text retrieval against ChromaDB. Every citation
the Reviewer agent emits is looked up in the corpus by article ID. On a miss,
the verifier returns the top-3 semantic candidates and forces the Reviewer to
re-cite. **A hallucinated citation cannot survive the audit trail.** In live
testing against Compass on three NDA examples, Loop 4 caught **4 hallucinated
citations across one data-broker NDA**, each rejected and corrected through
forced re-cite.

**Sovereign UAE positioning (three layers)**

| Layer | Implementation |
|---|---|
| Infrastructure | Compass (Core42 / G42), UAE-hosted, UAE-operated |
| Model | `G42-INCEPTION-GPT41-MSA` (Inception Arabic MSA) on the Interviewer for sovereign Arabic intake; `gpt-4.1` and `gpt-5.1` on Compass for downstream reasoning |
| Corpus | UAE Federal statutes sourced exclusively from the official UAE Legislation Portal (`uaelegislation.gov.ae`) |

See [`docs/architecture.md` §2](docs/architecture.md#2-solution-overview) for
the full solution writeup.

---

## 4. Agent Architecture

**10 agents** across two modes, orchestrated via LangGraph for explicit state,
branching, and loops.

```
POST /run (mode=build)  ──►  LangGraph build graph
                               Interviewer (EN/AR)
                                  │
                                  ▼
                               Debate ⇄ Architect      ← Loop 1 (stance debate)
                                  │   ▲                  Loop 3 (clarification)
                                  ▼   │
                               Builder ⇄ Validator      ← Loop 2 (validation reject)
                                  │
                                  ▼
                          copilot_id + config + audit_trail
                                  │
                                  ▼  (config persisted to data/copilots/)
POST /run (mode=use)    ──►  LangGraph use graph
                               Reviewer (emits findings)
                                  │
                                  ▼
                               Citation Verifier         ← Loop 4 (per finding)
                                  │   ▲                    exact-text corpus lookup;
                                  ▼   │                    hallucinated cites rejected,
                               Reviewer re-cite           Reviewer re-cites from candidates
                                  │
                                  ▼
                               Counter-Proposal Drafter
                                  │   ▲
                                  ▼   │                  ← Loop 5 (per finding)
                               Reviewer-as-critic
                                  │
                                  ▼
                               Synthesis
                                  │
                                  ▼
                          findings (verified) + summary + audit_trail
```

**Build Mode agents (6)**

| Agent | Role | Tier |
|---|---|---|
| Interviewer | Extracts structured requirements; accepts Arabic or English input, replies in the user's language | Standard (or Inception MSA on Compass) |
| Stance Debater A | Argues strict-compliance interpretation | Reasoning |
| Stance Debater B | Argues business-practical interpretation | Reasoning |
| Architect | Synthesizes requirements + debate into a copilot config | Reasoning |
| Builder | Instantiates the copilot with prompts, schema, retrieval rules | Standard |
| Validator | Runs sample inputs through the new copilot, rejects if substandard | Standard |

**Use Mode agents (4)**

| Agent | Role | Tier |
|---|---|---|
| Reviewer | Reviews documents against the org-tuned stance, emits findings | Reasoning |
| Citation Verifier | Validates every citation against the corpus via exact-text retrieval (orchestrator-driven, not an LLM call) | n/a |
| Counter-Proposal Drafter | Drafts replacement clauses for flagged risks | Standard |
| Synthesis | Assembles final output with confidence scores and audit trail | Standard |

- **All LLM access** goes through one wrapper, [`app/llm.py`](app/llm.py) (SOW §6).
- **Corpus**: 4 UAE Federal statutes ingested into ChromaDB, chunked by
  article. Citation Verifier uses exact-text retrieval so hallucinated cites
  are rejected with the real article text returned.
- **Audit trail**: every agent action and loop iteration logged to JSONL in
  `logs/`. Canonical evidence in `logs/samples/`.

See [`docs/architecture.md` §3](docs/architecture.md#3-agent-topology) for the
full topology, including the LangGraph state diagram.

---

## 5. Agent Collaboration Flow

Wakeel's 10 agents collaborate through **5 named feedback loops**. Every loop
iteration is logged to the audit trail with agent name, action, decision, and
reason. Hard caps guarantee termination.

| Loop | Mode | Agents | Behaviour | Cap |
|---|---|---|---|---|
| **Loop 1** — Stance debate | Build | Debater A/B ⇄ Architect | ≥2 rounds; Architect can request more | 3 rounds |
| **Loop 2** — Validation reject | Build | Validator → Builder | Reject substandard config; Builder revises | 3 iterations |
| **Loop 3** — Clarification | Build | Architect → Interviewer | Escalate ambiguity for follow-up | 2 escalations |
| **Loop 4** — Citation rejection | Use | Reviewer ⇄ Citation Verifier | Hallucinated cite → exact-text lookup fails → Reviewer re-cites from candidates | 3 per finding |
| **Loop 5** — Counter-proposal critique | Use | Drafter ⇄ Reviewer-as-critic | Vague/off-statute draft → Reviewer rejects → Drafter revises | 3 per finding |

**Sample runs (deterministic, committed evidence):**

- `logs/samples/build_mode_run_loops_1_2_3.jsonl` — full build-mode run with Loops 1, 2, 3 firing.
- `logs/samples/use_mode_run_loops_4_5.jsonl` — full use-mode run with Loops 4 and 5 firing on the aggressive vendor NDA.

**Per-call LLM trace:** [`logs/llm_calls.jsonl`](logs/).

See [`docs/architecture.md` §4](docs/architecture.md#4-feedback-loops) for
loop-by-loop deep dives.

---

## 6. Tools, Frameworks, and Models Used

**Tools and frameworks**

| Layer | Choice |
|---|---|
| Language | Python 3.11 |
| Agent orchestration | LangGraph |
| API | FastAPI |
| UI | Streamlit (chat-based, port 8001) |
| Vector store | ChromaDB (local, file-backed, CPU-only) |
| LLM SDK | OpenAI-protocol compatible (`openai` Python SDK) |
| LLM provider | Compass (Core42 / G42), OpenAI direct supported via env-var swap |
| Retry / backoff | tenacity |
| PDF ingestion | pypdf |
| Container | Docker |
| Logging | Python `logging` → JSONL files in `logs/` |

**Models** (confirmed available on Compass, defaults in `.env.example`)

| Tier | Env var | Model |
|---|---|---|
| Standard | `DEFAULT_MODEL` | `gpt-4.1` |
| Reasoning | `REASONING_MODEL` | `gpt-5.1` |
| Embedding | `EMBEDDING_MODEL` | `text-embedding-3-large` |
| Interviewer (optional sovereign) | `INTERVIEWER_MODEL` | `G42-INCEPTION-GPT41-MSA` (Inception Arabic MSA) |

The Interviewer override is per-agent via env only — no code change. If unset,
the Interviewer falls back to `DEFAULT_MODEL` with Arabic-aware prompting.

---

## 7. Data Sources

**UAE Federal statute corpus** — sourced exclusively from the official UAE
Legislation Portal (`uaelegislation.gov.ae`):

| Statute | File |
|---|---|
| UAE Federal Decree-Law 33 of 2021 (Labour Law) | `data/corpus/labour_law_33_2021.txt` |
| UAE Federal Decree-Law 45 of 2021 (Personal Data Protection Law) | `data/corpus/pdpl_45_2021.txt` |
| UAE Federal Law 18 of 1993 (Commercial Transactions Law) — key chapters | `data/corpus/commercial_transactions_18_1993.txt` |
| UAE Federal Law 5 of 1985 (Civil Transactions Law) — contract articles | `data/corpus/civil_transactions_5_1985.txt` |

Each statute is chunked by article with metadata (law name, article number,
exact text) and stored in ChromaDB. Ingestion is config-driven via
[`data/corpus/corpus_config.json`](data/corpus/corpus_config.json) — **adding
a fifth statute is a data + config drop, not a code change**.

> All four corpus files ship with **public placeholder excerpts** (clearly
> marked at the top of each text file). Replace with the founder's official
> full UAE PDFs as a data drop — no code change required.

**Synthetic NDA documents** for build and use mode examples live in
[`input_examples/`](input_examples/) — 5 build-mode inputs (including 3
Arabic) and 3 use-mode NDAs (aggressive vendor, balanced commercial,
data-broker). Corresponding expected outputs in
[`output_examples/`](output_examples/).

---

## 8. Repository Structure

```
wakeel/
├── app/                      Application code
│   ├── agents/               10 agent implementations (build + use)
│   ├── corpus/               Ingestion + retrieval pipeline
│   ├── graph/                LangGraph build + use graphs
│   ├── api.py                FastAPI POST /run + /copilots endpoints
│   ├── llm.py                Shared LLM wrapper (SOW §6)
│   ├── audit_humanizer.py    Audit-trail label humanizer (for the UI)
│   ├── copilot_registry.py   File-backed copilot persistence
│   └── ...
├── data/
│   ├── corpus/               4 UAE Federal statutes + corpus_config.json
│   ├── chroma/               ChromaDB vector store (gitignored)
│   └── copilots/             Minted copilot configs (gitignored)
├── input_examples/
│   ├── build_mode/           5 build-mode inputs (3 EN + 3 AR)
│   └── use_mode/             3 use-mode NDAs (aggressive, balanced, data-broker)
├── output_examples/          Expected POST /run responses for every input
├── logs/
│   ├── samples/              Canonical loop-firing evidence (committed)
│   └── llm_calls.jsonl       Per-call LLM trace (gitignored, generated)
├── tests/                    pytest suite (M1 + M2 acceptance gates)
├── docs/                     Architecture, evidence, verification, gates
├── demos/                    3 walkthrough video stubs + RUNBOOK.md
├── scripts/                  Generation + dev tooling (selectively committed)
├── run.py                    API entry point (port 8000)
├── run_ui.py                 Streamlit UI entry point (port 8001)
├── e2e_acceptance.py         Gate-by-gate acceptance report
├── metadata.json             Submission metadata (use_case_id, demo URL, etc.)
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## 9. Environment Variables

All configuration is via environment variables. Copy `.env.example` to `.env`
and edit locally — **never commit `.env`**.

| Variable | Purpose | Default in `.env.example` |
|---|---|---|
| **`OPENAI_BASE_URL`** | **LLM provider endpoint.** Compass = `https://api.core42.ai/v1` (the API endpoint per the Compass User Guide; note `compass.core42.ai` is the web UI, not the API). OpenAI direct = `https://api.openai.com/v1`. Same code, swap endpoint. | **`https://api.core42.ai/v1`** |
| `OPENAI_API_KEY` | LLM provider key. For Compass, this is the Core42 group key (kept client-side). Leave empty to force `OFFLINE_MODE`. | _(empty)_ |
| `DEFAULT_MODEL` | Standard tier (Interviewer fallback, Builder, Validator, Drafter, Synthesis). | `gpt-4.1` |
| `REASONING_MODEL` | Reasoning tier (Debaters, Architect, Reviewer). | `gpt-5.1` |
| `EMBEDDING_MODEL` | Embedding model for corpus + retrieval. | `text-embedding-3-large` |
| `INTERVIEWER_MODEL` | Optional per-agent override for the Interviewer (e.g. `G42-INCEPTION-GPT41-MSA` on Compass for sovereign Arabic). | _(empty — falls back to `DEFAULT_MODEL`)_ |
| `SAMPLE_MODE` | Legacy dev-only quota brake. Compass enforces group-level quotas, so this no longer alters wire behaviour. Leave `false` for acceptance / production runs. | `false` |
| `OFFLINE_MODE` | When `true` (or when `OPENAI_API_KEY` is empty), the LLM wrapper returns deterministic stubs — full graph, loops, UI, audit logs run with no credentials or quota. | `false` |
| `WAKEEL_BACKEND_URL` | Backend URL the Streamlit UI calls. | `http://localhost:8000` |
| `CHROMA_DIR` | Vector store path. | `./data/chroma` |
| `CORPUS_CONFIG` | Corpus manifest path. | `./data/corpus/corpus_config.json` |
| `LOG_DIR` | JSONL audit log directory. | `./logs` |

**Provider profile recap**

```env
# Developer (OpenAI direct) — day-to-day builds:
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=<your OpenAI direct key>

# Compass verification (UAE, client-side only) — M1/M2 acceptance:
OPENAI_BASE_URL=https://api.core42.ai/v1
OPENAI_API_KEY=<kept on the client side>
```

Same code, different endpoint — the SOW §6 wrapper handles both.

---

## 10. Setup Instructions

**Prerequisites**

- Python 3.11
- (Optional) Docker — only required for §12
- (Optional) OpenAI or Compass credentials — without them, `OFFLINE_MODE` runs
  the full system deterministically against built-in stubs

**Steps**

```bash
# 1. Clone
git clone https://github.com/hxcenteredai/wakeel.git
cd wakeel

# 2. Configure environment
cp .env.example .env
# Edit .env — set OPENAI_API_KEY for live mode, or leave empty for OFFLINE_MODE.

# 3. Create a virtual environment and install dependencies
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. Ingest the UAE statute corpus into ChromaDB
python -m app.corpus.ingest
# Expected: 4 statutes ingested, chunk counts logged per law.
```

---

## 11. How to Run Locally

**Both services (API + UI)**

```bash
python run.py        # FastAPI on http://localhost:8000
python run_ui.py     # Streamlit UI on http://localhost:8001
```

**Backend only**

```bash
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

**Health check**

```bash
curl localhost:8000/health
# {"status":"ok", "models":{...}, ...}
```

**End-to-end acceptance report**

```bash
python e2e_acceptance.py --offline                  # deterministic, no creds
python e2e_acceptance.py                            # uses .env (live LLM)
python e2e_acceptance.py --base-url http://localhost:8000   # against a running server
```

Runs the M1 customer journeys (Sarah/EN, Ahmed/AR, vague request) and the M2
use-mode journeys (aggressive vendor / balanced commercial / data-broker
NDAs), then prints **gate-by-gate verdicts for both M1 (Gates 1–6) and M2
(Gates 1–8)**. Exit code `0` only if all evaluated gates pass.

**pytest suite**

```bash
pytest tests/ -q                 # OFFLINE by default (fast, deterministic)
WAKEEL_E2E_LIVE=1 pytest tests/  # against the live LLM in .env
WAKEEL_E2E_BASE_URL=http://localhost:8000 pytest tests/   # against a running API
```

> **Verifying Milestone 1 from a fresh clone (incl. Compass):** see
> [`docs/verification.md`](docs/verification.md).
> **Verifying Milestone 2 (Use mode + Loops 4–5):** see
> [`docs/use-mode-evidence.md`](docs/use-mode-evidence.md).

---

## 12. How to Run with Docker

```bash
docker build -t wakeel .
docker run -p 8000:8000 -p 8001:8001 --env-file .env wakeel
```

Both API (port 8000) and Streamlit UI (port 8001) start cleanly from a fresh
clone. Memory at idle: ~137 MiB. Image size: ~1.2 GiB. Verified on macOS arm64
and Linux x86_64.

The image excludes `.venv/`, `.pytest_cache/`, `.cursor/`, `data/chroma/`,
`data/copilots/`, `logs/`, and `PO_requirements/` via `.dockerignore`, keeping
the build context small. `.env` and `.env.*` are also excluded so secrets
never enter the image.

---

## 13. API Usage

Single endpoint, two modes: **`POST /run`** on port 8000.

### Build Mode — mint a copilot

```bash
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{
    "mode": "build",
    "intake": {
      "workflow_description": "Review vendor NDAs against our conservative PDPL stance",
      "language": "en"
    }
  }'
```

**Response (full envelope):**

```jsonc
{
  "status": "success",
  "agents": [                                  // sourced from metadata.json
    {"name": "Interviewer", "role": "Extracts structured requirements; ..."},
    // ... 9 more entries
  ],
  "trace_id": "e319c0bf-1b46-472b-b17b-a8ec4f2179c9",   // equals run_id on success
  "log_file": "logs/run_e319c0bf-1b46-472b-b17b-a8ec4f2179c9.jsonl",
  "execution_time_seconds": 0.007077,          // wall-clock float
  // --- PRD §9 build payload spread underneath ---
  "run_id": "e319c0bf-1b46-472b-b17b-a8ec4f2179c9",
  "mode": "build",
  "copilot_id": "cp_a6944a76",
  "config": { /* full copilot config */ },
  "validation_results": { /* sample-run summary */ },
  "audit_trail": [ /* full agent interaction log */ ],
  "interviewer_response": "..."
}
```

Full samples in [`output_examples/build_mode/`](output_examples/build_mode/).

### Use Mode — review a document

The `copilot_id` from a Build Mode run is persisted to `data/copilots/` and
can be replayed against any document:

```bash
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{
    "mode": "use",
    "copilot_id": "cp_<from build>",
    "document": {
      "type": "text",
      "content": "<NDA body text>"
    }
  }'
```

**Response (same envelope, use-mode payload underneath):**

```jsonc
{
  "status": "success",
  "agents": [ /* same 10-agent inventory from metadata.json */ ],
  "trace_id": "9a10a611-...",
  "log_file": "logs/run_9a10a611-....jsonl",
  "execution_time_seconds": 0.004,
  // --- PRD §9 use payload spread underneath ---
  "run_id": "9a10a611-...",
  "mode": "use",
  "copilot_id": "cp_a6944a76",
  "findings": [ /* each with citation.verified=true */ ],
  "summary": { /* counts, risk buckets, recommendation banner */ },
  "audit_trail": [ /* per-agent + per-loop entries */ ]
}
```

**Error response (any /run failure):**

```jsonc
{
  "status": "error",
  "error": {
    "type": "validation_error",         // | "not_found" | "internal_error" | "client_error"
    "message": "use mode requires document.content",
    "recoverable": true                  // true for status < 500
  },
  "trace_id": "e502f469-...",            // fresh UUID even when no run started
  "log_file": "logs/run_e502f469-....jsonl",
  "detail": "use mode requires document.content"   // legacy back-compat mirror of error.message
}
```

The HTTP status code (`422`, `404`, `5xx`) is preserved on the response so
existing clients that switch on status keep working.

Full samples in [`output_examples/use_mode/`](output_examples/use_mode/).

### Supporting endpoints

```bash
curl localhost:8000/copilots                  # list all built copilots
curl localhost:8000/decisions/<copilot_id>    # decision history for a copilot
curl localhost:8000/health                    # health + model config
```

### Arabic intake

The Interviewer accepts **English or Arabic** input and replies in the user's
language (`interviewer_response`). All downstream structured fields remain in
English (per PRD §5 — Arabic output is v1.1 scope). Send `intake.language =
"ar"`; the UI auto-detects Arabic script and renders input + messages
right-to-left.

Optional: set `INTERVIEWER_MODEL=G42-INCEPTION-GPT41-MSA` on Compass to route
the Interviewer to Inception's Arabic-tuned MSA model (per-agent env override,
no code change).

---

## 14. Input and Output Examples

**Build Mode (5 inputs, 5 outputs)** in
[`input_examples/build_mode/`](input_examples/build_mode/) /
[`output_examples/build_mode/`](output_examples/build_mode/):

| # | File | Language | Scenario |
|---|---|---|---|
| 01 | `01_english_nda_fintech.json` | EN | Fintech NDA reviewer — canonical M1 build journey |
| 02 | `02_english_ambiguous.json` | EN | Ambiguous request — exercises Loop 3 (clarification) |
| 03 | `03_arabic_nda.json` | AR | Arabic vendor NDA workflow |
| 04 | `04_arabic_bank_pdpl.json` | AR | Arabic banking PDPL stance |
| 05 | `05_arabic_hospital_data.json` | AR | Arabic hospital data-handling (M2 Amendment §3 criterion 4) |

**Use Mode (3 inputs, 3 outputs)** in
[`input_examples/use_mode/`](input_examples/use_mode/) /
[`output_examples/use_mode/`](output_examples/use_mode/):

| # | File | Recommendation | Findings | L4 rejections | L5 critiques |
|---|---|---|---|---|---|
| 01 | `01_aggressive_vendor_nda.json` | DO NOT SIGN | 3 (1H/1M/1L) | 1 | 2 |
| 02 | `02_balanced_partner_nda.json` | Acceptable subject to revisions | 1 (Med) | 0 | 1 |
| 03 | `03_data_broker_nda.json` | DO NOT SIGN | 5 (2H/2M/1L) | 1 | 4 |

Each output is structurally valid (PRD §9 schema:
`run_id, mode, copilot_id, findings, summary, audit_trail`) with **every
finding carrying `citation.verified=true`**.

**Regenerate the use-mode examples** from a single command:

```bash
OFFLINE_MODE=true python3 scripts/generate_use_mode_examples.py
```

---

## 15. Logs and Traces

Every agent action and every loop iteration is logged to JSONL — both for
forensic auditability and for the live UI audit-trail panel.

| File | Contents | Status |
|---|---|---|
| `logs/run_<run_id>.jsonl` | Per-run audit trail (one file per `POST /run`) | Generated, gitignored |
| `logs/llm_calls.jsonl` | Per-LLM-call trace (model, tokens, latency, prompt+response hashes) | Generated, gitignored |
| `logs/samples/build_mode_run_loops_1_2_3.jsonl` | **Canonical** build-mode run showing Loops 1, 2, 3 firing | Committed |
| `logs/samples/use_mode_run_loops_4_5.jsonl` | **Canonical** use-mode run on the aggressive vendor NDA showing Loops 4 + 5 firing | Committed |

**Audit-trail entry schema** (per event):

```json
{
  "agent": "Citation Verifier",
  "action": "verify_citation",
  "loop": "Loop 4",
  "decision": "rejected",
  "reason": "Article not found in corpus; offered 3 candidates.",
  "details": { ... },
  "timestamp": "2026-06-05T11:24:08.412Z"
}
```

The Streamlit UI runs these entries through [`app/audit_humanizer.py`](app/audit_humanizer.py)
to render business-readable narratives (e.g. *"⟳ Loop 4: Citation rejected
(Federal Decree-Law 45 of 2021 Article 99 not found in corpus — Reviewer must
re-cite)"*) while the raw JSON remains accessible behind each expander for
engineers.

Loop firing across the committed canonical logs:

```
Build mode (1 run):  Loop 1 = 1 round, Loop 2 = 1 reject, Loop 3 = 1 escalation
Use mode (1 run):    Loop 4 = 1 reject, Loop 5 = 2 critiques
```

See [`docs/architecture.md` §8](docs/architecture.md#8-audit-trail) for the
full audit-trail schema and storage model.

---

## 16. Demo Video

**Drive folder (3 walkthrough videos):**
[https://drive.google.com/drive/folders/1nLXBq7jcrIG08VAtPjXvpC66VhmxSxEH?usp=sharing](https://drive.google.com/drive/folders/1nLXBq7jcrIG08VAtPjXvpC66VhmxSxEH?usp=sharing)

Matches `metadata.json` `demo_video_url`.

Per Milestone Amendment §3 criterion 6, the submission ships three technical
walkthrough videos:

| File | Scenario | Length |
|---|---|---|
| `demos/01_build_mode_walkthrough.mp4` | Build mode end-to-end (Loops 1–3 visible) | 60–90 s |
| `demos/02_use_mode_walkthrough.mp4` | Use mode with the Citation Verifier rejection moment (Loop 4) — the killer demo per PRD §16 | 60–90 s |
| `demos/03_arabic_intake_walkthrough.mp4` | Arabic intake (RTL rendering, Arabic Interviewer reply) | 60–90 s |

Recording runbook + storyboard: [`demos/RUNBOOK.md`](demos/RUNBOOK.md).

---

## 17. Known Limitations

| Limitation | Reason | Resolution |
|---|---|---|
| All four corpus statutes ship with public placeholder excerpts | The official founder-licensed UAE PDFs are a data drop, not a code change | Drop full PDFs into `data/corpus/` and re-run `python -m app.corpus.ingest` |
| `OFFLINE_MODE` uses deterministic stubs | Lets the full graph + UI + audit run without Compass credentials | Set real `OPENAI_API_KEY` for live behaviour |
| Use-mode offline stubs use hash-based pseudo-embeddings for semantic search | Determinism without an embedding API key | Live LLM mode produces semantically richer findings |
| Single tenant per deployment | v1.0 scope decision | v1.1 multi-tenant |
| Arabic output (findings, counter-proposals, summaries in Arabic) out of scope | PRD §5 and SOW §3 — Arabic intake is in-scope; output is v1.1 | v1.1 full bilingual |
| 4 UAE Federal statutes ingested | Citation Verifier reliability requires a clean corpus | v1.1 expansion (data-only) |
| No DMS integrations (iManage, NetDocuments, SharePoint) | Out of scope | v1.2 integrations |
| No persistent customer-correction learning | Out of scope | v1.2 continuous learning |
| Walkthrough videos in `demos/` are placeholder MP4 stubs at commit time | Real videos recorded in the founder's submission window | Replace per [`demos/RUNBOOK.md`](demos/RUNBOOK.md) |

See [`docs/architecture.md` §10](docs/architecture.md#10-design-decisions-and-trade-offs)
for the full design-decisions and trade-offs writeup.

---

## 18. Future Improvements

### v1.1 — Production hardening (1–2 months post-hackathon)

- Arabic findings output (full bilingual operation across all agents).
- Expanded corpus: full UAE Federal commercial and labour statutes (~30 laws).
- Free-zone law ingestion (DIFC, ADGM, and emirate-level frameworks).
- Customer portal for managing interpretation overrides.
- Multi-tenant architecture with copilot isolation per organization.
- Programmatic API for copilot lifecycle management.
- Initial pilot customers (2–3 UAE enterprises, 1 boutique law firm).

### v1.2 — Integration and monitoring (3–4 months post-hackathon)

- Real-time regulatory monitoring (Loop 6: Regulatory Change Detection on new
  gazette publications).
- Integration with major DMS systems (NetDocuments, iManage, SharePoint).
- Mobile UI for senior counsel approvals and escalation.
- SOC 2 Type 1 certification preparation.
- VAPT and security review completion.

### v2.0 — Platform expansion (6–12 months post-hackathon)

- Full agent customization SDK (customers configure agent personas via UI).
- Cross-jurisdiction corpus expansion (KSA, Egypt, Jordan as nearest-neighbor markets).
- Marketplace for industry-specific copilot templates.
- White-label deployment options for partner law firms.
- Continuous learning from customer corrections (feedback-into-copilot loop).

### v3.0 — Autonomous operations (12–24 months)

- Autonomous agent deployment within client compliance workflows.
- Predictive compliance (flag emerging regulatory risks before enforcement).
- Cross-statute reasoning (interdependencies between Labour Law and PDPL on
  the same document).
- Self-improving interpretation overrides.

See [`docs/architecture.md` §11](docs/architecture.md#11-future-scope-and-deployment-pathway)
for the full roadmap and deployment-pathway writeup, including the G42 /
Inception incubation stages.
