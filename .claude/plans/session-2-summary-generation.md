# Session 2 — Summary Generation Redesign (Track B)

> Read `.claude/plans/SESSIONS-OVERVIEW.md` first for working rules.
> Conversation in Hebrew. No file changes before approval. Show before/after for every edit.
> **Depends on Session 1 fix A2** (tokenized sections stored to SQLite) — verify it landed.

## Goal

Redesign the "יצירת סיכום ביקור" feature so the generated `.docx`:
1. Behaves like a **copy of an existing base document** from the patient folder (latest previous
   סיכום ביקור, else the סיכום אבחון), where the LLM changes *only* what the selected treatment
   sessions justify — untouched sections stay verbatim.
2. Has the **exact visual format of the clinic templates**
   (`tamplates/טמפליט אבחון בנות.docx` / בנים) — fonts, sizes, colors, margins, layout.
3. Contains real content under ממצאי האבחון (currently near-empty — see R3).
4. Uses **treatment-oriented headings** (e.g. סיכום טיפול, תמצית טיפול) instead of the
   diagnosis-oriented ones.
5. No PDF output.

## Current implementation (read before designing)

`app/generation/summary_generator.py` — workflow: `fetch_sources_step` (sessions + goals in date
range from SQLite, base document via `db.fetch_latest_family_a_as_dict`) → `generate_summary_step`
(prompt in `app/prompts/generation.py`, LLM returns JSON of 5 sections) → `build_doc_step`
(re-identify) → `save_doc_step` (builds a **bare `DocxDocument()` from scratch** — this is why
formatting is lost — writes SQLite rows, marks ingested, renders PDF).
UI trigger: `app/ui/gradio_app.py::generate_summary` (patient + date-range dropdowns).

**Privacy constraint (unchanged):** the LLM must never receive the raw file — only the
de-identified section texts already stored in SQLite. The physical file/template is used purely as
a local formatting skeleton into which re-identified output is injected *after* generation.

## Known facts established by prior investigation

- **Root cause of "document comes out empty":** LlamaIndex `Anthropic` defaults to
  `max_tokens=512`; the 5-section Hebrew JSON gets truncated → `parse_generated_sections`
  (`app/prompts/generation.py`) silently falls back to all-empty sections. The user already added
  `max_tokens=10000` (uncommitted) — formalize it (recommend a `config.yaml` key) and **fix the
  silent fallback**: a parse failure must return a user-facing error, never an empty document (B4).
- **R3 — the ממצאי האבחון gap:** during ingestion, `app/ingestion/adapter_a.py` routes everything
  under a recognized domain sub-header into `domain_findings` (13 domains + parent mapping in
  `PARENT_DOMAINS`); `family_a_sections.ממצאי_האבחון` keeps only the intro line
  ("להלן תיאור יכולותיו בתחומים השונים:"). So the base document JSON sent to the LLM has an empty
  findings section. The domain texts live in the `domain_findings` table keyed by
  `(patient_id, session_date, domain_name)`.
- **Template structure** (inspected with python-docx): formatting lives in the document defaults
  (styles.xml) and bold runs; no tables; a footer part exists. Building a document from scratch
  with python-docx can never reproduce it — the skeleton must come from copying an existing file.

## Design questions to resolve in-session (present with problem / before-after / deletions / trade-offs)

**Q1 — Formatting skeleton source:**
- Option A: copy the **base document file** itself (the actual latest summary/diagnosis in the
  patient folder) and replace section bodies in-place. Pro: inherits everything including any
  per-patient quirks; matches the "copied file" mental model. Con: needs robust section-boundary
  detection inside the docx; a malformed prior file propagates.
- Option B: keep a clean **bundled template** (from `tamplates/`) as the skeleton, fill header
  fields + section bodies. Pro: guaranteed template look, stable; Con: "copy of base document"
  is then achieved semantically (LLM keeps unchanged sections verbatim) rather than physically.
- Either way: replacement = locate heading paragraph → delete body paragraphs until next heading →
  insert new paragraphs inheriting the template body formatting.

**Q2 — Merging domain findings into the base document.** Options: (a) at generation time, compose
the findings section from `domain_findings` rows matching the base document's `session_date`
(use `db.fetch_domain_finding`-style queries; mind parent domains); (b) give the LLM sections and
domains as separate JSON keys and render domains as sub-headings in the output (visually closest
to the template); (c) change ingestion to duplicate composed text into `family_a_sections` (rejected
unless a+b fail — data duplication). Recommend (b) or (a); decide with the user, including whether
the generated summary keeps per-domain sub-headings.

**Q3 — Treatment-oriented headings.** User wants e.g. סיכום טיפול / תמצית טיפול ("וכדומה") —
**get the exact five headings from the user**. Critical consistency issue: if a clinician later
edits the generated file, it is re-ingested through `adapter_a.py`, whose `SECTION_HEADERS` only
recognizes the diagnosis wording — sections would silently come back empty. The adapter must
recognize the new headings for `clinic_visit_summary` files (keep the SQLite `section` keys
unchanged; map both heading sets to the same keys). Update `SECTION_LABELS` in
`summary_generator.py` accordingly.

**Q4 — max_tokens location:** hardcoded vs `config.yaml` (`anthropic.max_tokens_generation`).
Recommend config.

## Tasks (after design approval)

- B1: formalize `max_tokens` (+ B4: parse failure → Hebrew error to user, log the raw LLM output).
- B2: implement the chosen skeleton architecture + domain-findings merge + new headings
  (incl. adapter_a heading recognition for re-ingest).
- B3: delete `_save_pdf` and all PDF plumbing; remove `reportlab`, `python-bidi` from
  `pyproject.toml`; `save_doc_step` result and the UI status display simplify accordingly.
- While editing `save_doc_step`: compute the stored file hash from the bytes actually written to
  disk (single serialization) — cheap to fix in passing (user deprioritized it; ask first).

## Definition of done

- Generate produces a fully-populated document that visually matches the template, with unchanged
  sections preserved verbatim, treatment-oriented headings, no PDF.
- Editing + re-ingesting a generated file round-trips correctly (sections parsed, de-id applied).
- PLAN.md Step 7 (and Step 2 if headings/sections mapping changed) rewritten and approved.
- Update `SESSIONS-OVERVIEW.md` status table.
