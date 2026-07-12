# Fix Roadmap — Sessions Overview

> Created: 2026-07-09. Branch: `clinical-rag`.
> This document is the master index for four focused fix sessions. Each session has its own
> plan file in this directory. Start a session by telling Claude:
> *"Read `.claude/plans/session-N-….md` and let's begin."*

## Project one-liner

Clinical RAG Q&A system for a speech-language pathology clinic. Hebrew patient `.docx` files are
ingested, de-identified (mandatory gate), stored in local SQLite + Pinecone (treatment plans only),
and queried via Claude Haiku through a Gradio UI. Full architecture: `PLAN.md` (project root).

## Session order and scope

| # | Session | Plan file | Scope | Status |
|---|---------|-----------|-------|--------|
| 1 | Privacy & Safety | `session-1-privacy-safety.md` | NER wiring + fail-closed, PII written to DB by generate flow, leftover PII logs, localhost binding | ✅ done 2026-07-09 |
| 2 | Summary Generation Redesign | `session-2-summary-generation.md` | Document built from base-document copy, exact template formatting, domain findings merge, treatment-oriented headings, remove PDF | ✅ done 2026-07-10 |
| 3 | Answer Quality & Routing | `session-3-answers-routing.md` | Dates missing from LLM context, latest-document fetch bug, Q&A max_tokens, full routing review | pending |
| 4 | Ops & Cleanup | `session-4-ops-cleanup.md` | Pinecone index rename, project-wide logging, start.bat warnings, Cohere embedding batching (96/call), full PLAN.md rewrite | pending |

### Session 1 results (2026-07-09)

- **A1** — `run_mode` added to `config.yaml` + `AppConfig`; NER gate at `run_ingestion` entry
  (production = fail-closed with Hebrew error, test = warning); Pass 2 NER re-scan fails closed
  in production. All acceptance tests passed.
- **A2** — generate flow now stores tokenized sections in SQLite
  (`ReidentifiedDocEvent.tokenized_sections`); re-identified text only in the `.docx`.
- **A3** — found already fixed by the user before the session (both PII logs removed).
- **A4** — server binds to `127.0.0.1`.
- **Cleanup** — user approved full wipe: SQLite DB + all Pinecone vectors deleted, everything
  re-ingested through the fixed pipeline (10/10 ok; generated summary files had already been
  deleted from disk by the user). `run_mode` is currently `"test"` — switch to `"production"`
  after running `setup.bat` (the ONNX model has never been converted on this machine).
- PLAN.md updated: Step 3 (Run Mode amendment), Step 7 (SQLite record wording), Step 9 (localhost).

### Session 2 results (2026-07-10) — branch `session-2-summary-generation`

- **B1** — `anthropic.max_tokens_generation: 10000` in config.yaml + AppConfig.
- **B4** — parse failure raises `GenerationParseError` → Hebrew error + raw LLM output logged;
  silent empty-document fallback removed.
- **B2** — new `app/generation/docx_builder.py`: skeleton = copy of the base document's physical
  file, fallback = `tamplates/טמפליט אבחון בנים.docx` (the up-to-date template; בנות is unused);
  section bodies replaced in place, headings re-worded (מהלך/ממצאי הטיפול, הנדון אבחון→טיפול),
  תמצית deleted; domain findings rendered as bold sub-headings; formatting inherited via
  pPr/rPr cloning. Domains flow end-to-end: adapter + pipeline extract/store them for
  clinic_visit_summary too; generation sends them as a separate JSON key (ממצאי_תחומים) and
  writes them back to `domain_findings`.
- **B3** — PDF removed (`_save_pdf`, reportlab, python-bidi; `uv sync` run — note it also
  uninstalled the `setup` extras torch/optimum; `setup.bat` reinstalls them when needed).
- Generated filename is now `סיכום טיפול N <name>.docx`; the adapter no longer recognises
  `סיכום ביקור`. File hash now computed from the bytes written to disk (single serialization).
- Extra approved fixes: Pass 2 validation now covers domain texts; adapter signature sentinel
  extended (paragraph starting with בברכה stops section collection).
- Verified by a smoke test (template + base-document skeletons, full re-ingest round-trip);
  pytest: only the 2 known routing failures (Session 3) remain.
- PLAN.md updated: Step 2 (family_a_sections note, domain_findings schema + both types,
  section-headers amendment), Step 7 (Generate redesign, error-handling row).

Session 2 depends on Session 1 (fix A2 must land first — see session 1). Sessions 3 and 4 are
independent but should run after 1–2 to avoid merge noise.

## Working rules (apply to every session — non-negotiable)

1. **Conversation in Hebrew; all plan documents and prompts in English.**
2. **No file changes before explicit user approval.** Workflow per step:
   ask ALL open questions → reach agreement → present final decisions → get approval →
   only then update PLAN.md and code.
3. **Every code change is shown as exact before/after snippets before editing.**
4. Open questions must be presented with: the problem, before/after, what gets deleted,
   and trade-offs — never bare option labels.
5. At the end of each session, after approval: update `PLAN.md` to reflect the decisions,
   and mark the session's items as done in this file.
6. Re-read the actual current code at session start — do not trust these documents blindly;
   the user edits code between sessions.

## Decisions already locked by the user (2026-07-09)

- **Server binding:** change to `127.0.0.1` (no LAN access; no auth needed for now).
- **NER / de-id gate:** add `run_mode: production | test` to `config.yaml`.
  In `production`, missing NER model **blocks ingestion** (fail-closed). In `test`, proceed with warning.
- **ONNX stays** (best practice for CPU inference; the abandoned WIP refactor to PyTorch was reverted).
- **PII in logs is acceptable to the user** (local machine only). Logging policy in PLAN.md must be
  updated accordingly in Session 4. Desired: log the question, the exact context sent to the LLM,
  and the answer.
- **PDF generation is removed** (Session 2), including `reportlab` / `python-bidi` deps.
- **Pinecone index renamed** to `clinical-rag`; the wrongly-named `rag-index` gets deleted (Session 4).
- Real diagnosis files are named `סיכום אבחון …` — the code's `startswith` checks are correct;
  PLAN.md and the adapter docstring need aligning, not the code.
- Findings explicitly de-prioritized by the user: generated-file hash double-serialization,
  same-date session overwrite, failing router tests (superseded by the Session 3 routing review).

## Known uncommitted work-tree state (updated 2026-07-10)

All Session 2 changes live uncommitted on branch `session-2-summary-generation`
(code + PLAN.md + this file). Discuss committing checkpoints with the user at
each session end (do not commit without being asked).
