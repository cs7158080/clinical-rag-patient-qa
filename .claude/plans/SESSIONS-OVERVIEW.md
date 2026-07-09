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

| # | Session | Plan file | Scope |
|---|---------|-----------|-------|
| 1 | Privacy & Safety | `session-1-privacy-safety.md` | NER wiring + fail-closed, PII written to DB by generate flow, leftover PII logs, localhost binding |
| 2 | Summary Generation Redesign | `session-2-summary-generation.md` | Document built from base-document copy, exact template formatting, domain findings merge, treatment-oriented headings, remove PDF |
| 3 | Answer Quality & Routing | `session-3-answers-routing.md` | Dates missing from LLM context, latest-document fetch bug, Q&A max_tokens, full routing review |
| 4 | Ops & Cleanup | `session-4-ops-cleanup.md` | Pinecone index rename, project-wide logging, start.bat warnings, Cohere embedding batching (96/call), full PLAN.md rewrite |

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

## Known uncommitted work-tree state (as of writing)

- `app/generation/summary_generator.py` — user added `max_tokens=10000` to the LLM constructor.
- `app/ingestion/pipeline.py` — user removed one debug log (two PII logs remain, see Session 1).
- `app/storage/pinecone_client.py` — user removed Cohere debug logs.

Nothing has been committed since `b75f138`. Discuss committing checkpoints with the user at
each session end (do not commit without being asked).
