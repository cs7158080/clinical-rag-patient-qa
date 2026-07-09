# Session 1 — Privacy & Safety Fixes (Track A)

> Read `.claude/plans/SESSIONS-OVERVIEW.md` first for working rules and locked decisions.
> Conversation in Hebrew. No file changes before approval. Show before/after for every edit.

## Goal

Restore the de-identification gate to its intended "mandatory, blocking, safety-critical" status
(PLAN.md Step 0), and stop the two active PII leaks. Small, surgical fixes — most design decisions
are already locked.

## Background — how de-id is supposed to work (PLAN.md Steps 3, 9)

Pass 1 (`app/deidentification/deid.py::deidentify_text`): deterministic patient-name replacement →
Hebrew NER (heBERT, ONNX) for third-party PER/ORG entities → regex for national ID / phone / email.
Pass 2 (`app/deidentification/validation.py::validate_deidentified`): regex scan + re-id-map scan +
NER re-scan. A file failing the gate is blocked from embedding/upload.

## Tasks

### A1 — Wire the NER model + fail-closed behavior (the critical one)

**Problem:** `load_ner_model()` (`app/deidentification/ner.py:47`) is **never called anywhere**.
Therefore `is_model_loaded()` is always False, so:
- Pass 1 silently skips NER (`deid.py` — the `if is_model_loaded():` branch) → third-party names
  (parents, teachers, siblings) and institution names in free text are sent un-masked to
  Cohere/Pinecone/Claude.
- Pass 2's NER re-scan (`validation.py::_check_ner_rescan`) returns True when the model is not
  loaded → no safety net either.

**Locked decisions:**
- Add `run_mode: "production" | "test"` to `config.yaml` + `AppConfig` (`app/config.py`).
- `production`: if the NER model cannot be loaded, **ingestion must be blocked** with a clear
  Hebrew error telling the user to run `setup.bat` (fail-closed). This applies to Pass 2's
  re-scan as well — in production, an unloaded model must fail validation, not skip it.
- `test`: proceed without NER, with a prominent WARNING.

**To design in-session (ask the user):**
- Where to call `load_ner_model(config.models_dir)`: recommended at the start of the ingestion
  entry points — `run_ingestion()` in `app/ingestion/pipeline.py` and/or the UI `ingest_files()`
  handler in `app/ui/gradio_app.py` — NOT at app start (PLAN.md says the model should not load
  for query-only usage).
- Exact config key name and default (recommend defaulting to `production` so a missing key is safe).
- How `validation.py` learns the run mode (parameter vs config import) — keep it simple.

**Acceptance:** in production mode with `models/heBERT_NER_onnx/` absent, pressing the ingestion
button yields a blocking Hebrew error and no file is processed. With the model present, ingesting a
synthetic file containing a third-party name produces a `PERSON_xxx` token in the stored text and a
WARNING in the log. In test mode, ingestion proceeds with a warning.

### A2 — Generate flow writes RE-IDENTIFIED text into the de-identified table

**Problem:** in `app/generation/summary_generator.py`, `build_doc_step` re-identifies the sections
(real names restored) and passes them onward in `ReidentifiedDocEvent`. `save_doc_step` then writes
**those** sections into `family_a_sections.text_deidentified` (the loop around lines 271–280).
Consequences: real PII sits in the supposedly-anonymous table, and the next Q&A about a visit
summary sends real names to Claude. It also poisons the base document used by the next
generate run.

**Fix direction (present exact before/after in-session):** carry the tokenized sections (from
`TokenizedDocEvent`) through to `save_doc_step` alongside the re-identified ones — write tokenized
text to SQLite, use re-identified text only for the `.docx` body.

**DB cleanup (needs explicit user approval before running anything):** existing
`family_a_sections` rows produced by previously generated summaries are contaminated. They are
identifiable by `source_file_path` pointing at generated `סיכום ביקור N …` files. Proposal: delete
those rows and their `ingested_files` entries; the files can be regenerated or re-ingested through
the (now fixed) de-id pipeline. Inspect and show the user what will be deleted first.

### A3 — Remove the two remaining PII debug logs

`app/ingestion/pipeline.py:203` — `logger.info(ev.parsed_data.get("header", {}).get("name"))`
logs the raw patient name. `app/ingestion/pipeline.py:334` — `logger.info(ev.parsed_data)` logs the
full parsed payload including real header name + national ID. Both are debug leftovers; delete.
(Note: the user later wants deliberate, structured logging — Session 4 — and accepts PII in logs;
these two lines are noise regardless.)

### A4 — Bind the server to localhost

`app/main.py:46`: change `server_name="0.0.0.0"` → `"127.0.0.1"`. Locked decision; no discussion
needed beyond showing the diff.

## Definition of done

- All four fixes approved, applied, and demonstrated (run the app, run one ingestion in each mode).
- `PLAN.md` updated: Step 3 (fail-closed + run_mode), Step 7 (generate flow stores tokenized text),
  Step 9 (localhost binding). Get approval for the PLAN.md wording before editing it.
- Update `SESSIONS-OVERVIEW.md` status table.
