# RAG Agent — Patient Clinical Q&A — Design Plan

> Status: **in progress, step-by-step.** Each step is locked only after explicit agreement.
> Discussion language: Hebrew. This document: English.

## Context

A speech-language clinic (קלינאית תקשורת). Data is a root folder → one subfolder per patient
(unique names) → files based on a small set of templates. Queries are **mostly temporal**
("what did we work on in the last 2 months", "progress over 3 months", "have we worked on
auditory memory — when and with what results"), plus filtering by template type
(diagnosis vs treatment), and point-in-time lookups.

**Data is REAL PHI.** Regulatory frame is Israeli (Privacy Protection Law + data-security
regulations + Ministry of Health), not HIPAA.

## Agenda (locked)

| # | Step | Status |
|---|------|--------|
| 0 | Foundational decisions: deployment & privacy posture | ✅ **LOCKED** |
| 1 | Data model & templates (structure, where dates live, file = one unit vs many sessions) | ✅ **LOCKED** |
| 2 | Metadata schema & chunking | ✅ **LOCKED** |
| 3 | De-identification layer | ✅ **LOCKED** |
| 4 | Indexing & storage | ✅ **LOCKED** |
| 5 | Query understanding & routing (NL → structured query; Hebrew relative dates) | ✅ **LOCKED** |
| 6 | Retrieval modes (filter-then-summarize vs filter+semantic; large-context map-reduce) | ✅ **LOCKED** |
| 7 | Answer generation & re-identification | ✅ **LOCKED** |
| 8 | Evaluation & testing | ✅ **LOCKED** |
| 9 | Code architecture, runtime, ops, logging, error handling | ✅ **LOCKED** |
| 10 | UI Design (Gradio) — layout, colour palette, components, UX | ✅ **LOCKED** |

---

## Step 0 — Deployment & Privacy Posture  ✅ LOCKED

**Decision:** Cloud stack — **Cohere** (embeddings) + **Pinecone** (vector store), orchestrated by
**LlamaIndex**. Generation LLM: TBD (decided in step 7; likely external).

**Binding consequences (carried into later steps):**

1. **De-identification is a mandatory, blocking, safety-critical gate (step 3).** No text reaches
   Cohere or any external LLM before it passes verified de-identification. If de-id fails on a
   document, that document is NOT sent. This is the system's single point of failure and must be
   designed and tested to the highest standard.
2. **Legal assumption (explicit, out of code scope):** a lawful basis / patient consent exists for
   transferring **de-identified** health data to external cloud providers, with DPAs in place for
   Cohere and Pinecone.
3. **Nothing identifying enters Pinecone.** Embeddings are derived from de-identified text only.
   Pinecone metadata stores an anonymous `patient_id` (hash/token), never raw identifiers
   (no national ID `ת.ז.`, no full name). Re-identification map is kept **locally**.

**Rejected alternatives:** Fully-local stack (recommended for real PHI but declined for build speed
/ Hebrew embedding quality); local-corpus + external-LLM hybrid.

---

## Step 1 — Data Model & Templates  ✅ LOCKED

### Template types (3 types, 2 structural families)

| template_type | Filename pattern | Dated units per file | Structural family |
|---|---|---|---|
| `diagnosis` | `אבחון בנים/בנות <name>.docx` | 1 (report date) | Family A |
| `clinic_visit_summary` | `סיכום ביקור <number> <name>.docx` | 1 (visit date) | Family A |
| `treatment_plan` | `תוכנית טיפול הדרכתי <name>.docx` | Many (dual-layer) | Family B |

### Filesystem as primary metadata oracle
Patient folder = `clients/<patient_name>/`. Folder name → `patient_name` (unique, deterministic).
Filename prefix → `template_type`. No file needs to be opened to know patient or type.
Patient name in filename suffix = validation only (redundant with folder).

### Family A — diagnosis & clinic_visit_summary (identical structure)
**File-level PII:** `שם` (name), `ת.ל.` (date of birth), `ת.ז.` (national ID), HMO name.
**Date source:** `תאריך` field at top of document. One date per file.
**Fixed sections:** רקע / מהלך האבחון / ממצאי האבחון / סיכום והמלצות / תמצית אבחון.
**Fixed findings taxonomy (13 domains, diagnosis only):**
פרגמטיקה ותקשורת, הבנת שפה, הבעת שפה, לקסיקון, תחביר, מורפולוגיה,
התארגנות להבעת מלל מורכב, מובנות הדיבור, מודעות פונולוגית, זיכרון שמיעתי, אורל מוטור,
אכילה, שטף.
→ Domains are indexable as structured metadata, not only free text.
**Signature block** (clinician name + `מ.ר.` license): clinician PII, not patient PII.

### Family B — treatment_plan (dual-layer structure)
**File-level fields:** `שם`, `ת.ל.`, `תאריך התחלת הטיפול`, `מטרת על`, `הערות`.

**Layer 1 — Goals table** (top of file):
Each row = one dated snapshot: `(session_date, goals_text)`.
New dates are added as new rows. Each row → one independent chunk with its own `session_date`.

**Layer 2 — Session summaries** (free-text blocks appended over time):
Each block = `date + "עבדנו על... התגובה הייתה..."`.
Each block → one independent chunk with its own `session_date`.
**This layer is the primary source for "what did we do and what were the results" queries.**

### Adapter architecture (2 adapters, not 3)
**Adapter A** (shared base, `template_type` as parameter):
Reads Family A files → emits exactly 1 chunk with date + sections.
Clinician signature block (name + מ.ר.) is omitted from extracted text — stripped before any downstream processing (decided in Step 3).

**Adapter B** (treatment plan only):
Reads Family B files → emits N chunks from Layer 1 (goals rows) + M chunks from Layer 2
(session summaries). Single file → N+M independent dated chunks.

---

## Step 2 — Metadata Schema & Chunking  ✅ LOCKED

### SQLite — 6 tables

**`family_a_sections`** — diagnosis & clinic_visit_summary (same structure, same adapter)
Note: records in this table are written via two paths — (1) the ingestion adapter reading a file
from disk, and (2) the generate feature writing directly after creating a clinic_visit_summary.
Both paths produce the same schema; `source_file_path` is NULL for generated summaries,
distinguishing the two origins.
```
patient_id        TEXT   -- anonymous hash
template_type     TEXT   -- 'diagnosis' | 'clinic_visit_summary'
session_date      TEXT   -- ISO 8601
section           TEXT   -- 'רקע' | 'מהלך_האבחון' | 'ממצאי_האבחון' | 'סיכום_והמלצות' | 'תמצית_אבחון'
text_deidentified TEXT
source_file_path  TEXT   -- path of source .docx; NULL for generated summaries
PRIMARY KEY (patient_id, template_type, session_date, section)
```

The composite primary key `(patient_id, template_type, session_date, section)` is the natural choice for this table. The architecture guarantees at most one Family A file per `(patient, type, date)`, and each such file contains a fixed, non-repeating set of sections. No surrogate key is needed; the domain structure itself enforces uniqueness.

**`treatment_goals`** — Layer 1: one row per goals-table row
```
patient_id                TEXT
session_date              TEXT   -- ISO 8601
goals_text_deidentified   TEXT
pinecone_id               TEXT
source_file_path          TEXT
PRIMARY KEY (patient_id, session_date)
```

**`treatment_sessions`** — Layer 2: one row per session block
```
patient_id                  TEXT
session_date                TEXT   -- ISO 8601
session_text_deidentified   TEXT
pinecone_id                 TEXT
source_file_path            TEXT
PRIMARY KEY (patient_id, session_date)
```

**`domain_findings`** — 13 fixed domains, extracted from diagnosis ממצאי האבחון
```
patient_id               TEXT
session_date             TEXT   -- ISO 8601 (matches parent diagnosis row)
domain_name              TEXT   -- fixed set: פרגמטיקה ותקשורת | הבנת שפה | הבעת שפה |
                                --   לקסיקון | תחביר | מורפולוגיה | התארגנות להבעת מלל מורכב |
                                --   מובנות הדיבור | מודעות פונולוגית | זיכרון שמיעתי |
                                --   אורל מוטור | אכילה | שטף
domain_text_deidentified TEXT
PRIMARY KEY (patient_id, session_date, domain_name)
```

**`ingested_files`** — one row per processed source file, used for re-ingest detection
```
file_path      TEXT PRIMARY KEY
file_hash      TEXT   -- SHA256 of file content
ingested_at    TEXT   -- ISO 8601
```
Re-ingest check: `SELECT file_hash FROM ingested_files WHERE file_path = ?`.
Generated `clinic_visit_summary` files: row inserted after serialization with the known hash → re-ingest skips them as unchanged.

**`patient_metadata`** — static header fields per patient, populated during ingestion of any Family A file
```
patient_id   TEXT
field_name   TEXT   -- 'date_of_birth' | 'hmo_name' | 'national_id'
value        TEXT   -- real (unmasked) value
PRIMARY KEY (patient_id, field_name)
```
`date_of_birth` (ת.ל.) and `national_id` (ת.ז.) are stored here as real values for direct use by the generate feature. Dates are not tokenized (Step 3); `national_id` is tokenized in document text but the real value is retained locally here for header reconstruction.
Used by the generate feature to populate the header of new `clinic_visit_summary` files.

### Pinecone — treatment_plan only

Only `treatment_goals` and `treatment_sessions` rows are embedded.
**Metadata stored per vector:**
```
patient_id    -- for mandatory pre-filter
session_date  -- for temporal pre-filter
chunk_type    -- 'goals_row' | 'session_summary'
              -- (used to route back to the correct SQLite table after retrieval)
```
`template_type` is NOT stored — everything in Pinecone is treatment_plan by definition.

### Chunking & segmentation

| Source | Method | Unit |
|---|---|---|
| Family A sections | python-docx paragraph scan, detect section headers | 1 SQLite row per section |
| Layer 1 goals | python-docx table rows (direct API) | 1 row per table row |
| Layer 2 sessions | Multi-format date parser (see *Session boundary detection* below) | 1 row per session block |

No fixed character-limit splitting. Structure of the document drives all boundaries.

### Session boundary detection

Layer 2 session boundaries are detected line-by-line. A line is treated as a session-start marker
if it can be parsed as a valid date under any of the following formats, attempted in order:

| Priority | Format | Example |
|---|---|---|
| 1 | DD/MM/YYYY | 07/03/2025 |
| 2 | D/M/YYYY | 7/3/2025 |
| 3 | DD.MM.YYYY | 07.03.2025 |
| 4 | D.M.YYYY | 7.3.2025 |

Each format is tried in sequence; the first successful parse wins. Lines that match no format are
treated as continuation text belonging to the current session block. All parsed dates are
immediately normalized to ISO 8601 (`YYYY-MM-DD`) and stored in `session_date`. The original
date line is not retained as text content. The mechanism is fully deterministic; all supported
formats are unambiguous within the DD/MM/YYYY family.

### Date handling

- Stored as ISO 8601 (`YYYY-MM-DD`) in all SQLite columns and Pinecone metadata.
- Original Hebrew/numeric format is preserved inside `text_deidentified`.
- LLM is instructed to write dates in Hebrew in the final answer.

### Retrieval logic

| Query type | Route |
|---|---|
| `check_domain` | `domain_findings` WHERE domain_name = ? → LLM |
| `summarize` / `find_specific` on diagnosis or clinic_visit_summary | `family_a_sections` fetch all (concat) → LLM |
| `clinic_visit_summary` any intent | date resolution per Step 6 |
| `summarize` on treatment_plan | `treatment_sessions` DATE filter → fetch all → LLM |
| `find_specific` on treatment_plan | Pinecone semantic + metadata filter → LLM |

### Generate clinic_visit_summary feature (future)

The existing storage already supports this use case:
- Layer 2 session rows → `treatment_sessions` (all sessions accessible by date range)
- Previous visit summaries → `family_a_sections` WHERE template_type='clinic_visit_summary' (all, not just latest)

This is a **generation** use case (not retrieval). Full design deferred to Step 7.

### Error Handling — Amendment (Step 9)

| Situation | Behaviour |
|---|---|
| `.docx` file is corrupted or unreadable | `python-docx` raises an exception. The ingestion pipeline catches it, writes an `ERROR` entry to the log (file path only, no content), skips the file, and continues to the next. |
| Layer 2 (`treatment_plan`) contains no session-date markers | The entire Layer 2 text is treated as a single block. `session_date` is set to the file-level date from the header. A `WARNING` is written to the log. |
| A Family A section header is not found in the document | The section is stored with `text_deidentified = ""`. No error is raised; sections are considered optional. |

---

## Step 3 — De-identification Layer  ✅ LOCKED

### PII Taxonomy

| Category | Fields |
|---|---|
| **Sensitive (de-identify)** | Name, ת.ז. (national ID), educational institution, phone, email |
| **Not sensitive (kept as-is)** | All dates (including ת.ל. / date of birth), HMO name |

### Tooling (Hybrid)

| Target | Method |
|---|---|
| Header fields (known positions) | Positional extraction / regex |
| Patient's own name (pre-NER) | Deterministic replacement using three sources: (1) patient folder name (canonical), (2) document filename, (3) header `שם` field. A source is accepted as a valid variant only if its last name matches the canonical last name from source 1. Only full-name combinations (first name + last name) are replaced; first name alone is never replaced. If any source contains a different last name, ingestion stops and an error is logged. |
| Additional person names in free text | Local Hebrew NER model (e.g., `avichr/heBERT_NER`) — runs after patient name is already replaced; responsible only for names that are not the patient's |
| ת.ז. in free text | Regex (9-digit sequence) |
| Educational institutions in free text | NER (ORG entity) |
| Phone numbers | Regex (known formats) |
| Email addresses | Regex |
| Clinician signature block (name + מ.ר.) | Omitted at Adapter layer — never reaches de-id (see Step 2 amendment) |

### Token Format

All detected PII → unique, reversible token using full SHA256:

| PII type | Token |
|---|---|
| Person name | `PERSON_{sha256(name)}` |
| National ID | `ID_{sha256(value)}` |
| Educational institution | `INST_{sha256(name)}` |
| Phone | `PHONE_{sha256(value)}` |
| Email | `EMAIL_{sha256(value)}` |

Hash-based tokens are deterministic: same entity in different files → same token, no additional state required.

**`patient_id`** (stored in SQLite and Pinecone) = `sha256(patient_folder_name)` — identical to the
PERSON token hash for the patient. A single re-identification map therefore serves both purposes.

**Re-identification map** (local JSON file):
`{ sha256(entity): original_value }` — covers all PII types and all detected entities (not only patients).

### Gate — Two-Pass

**Pass 1 (de-identification):** applied in the following order:
1. Header fields extracted via positional regex (name, ת.ז., and other structured header fields).
2. **Patient name pre-replacement:** the patient's canonical name is the folder name (source 1).
   Sources 2 (filename) and 3 (header `שם`) are checked for variants. A source is a valid variant
   if and only if its last name matches the canonical last name from source 1 (different first
   names are accepted as shortenings, e.g. יוסי / יוסף). If any source contains a different last
   name, ingestion stops immediately and an error is written to the log (document path and conflict
   type; no name values).
   All accepted variants are collected. Every occurrence of every `(first_name + last_name)`
   combination in the document is replaced with `PERSON_{sha256(patient_folder_name)}`.
   First name alone is never replaced — partial-name occurrences without a last name are left for
   the NER pass.
3. **Hebrew NER** (`avichr/heBERT_NER`) over the remaining free text: detects PER entities (additional person names that are not the patient's) and ORG entities (educational institutions), and replaces each with its `PERSON_xxx` or `INST_xxx` token.
   - **Warning log:** whenever NER detects a PER entity that is not the known patient name, a `WARNING` entry is written to the system log containing the entity's token ID (e.g., `PERSON_a3f7...`) and the source document path. The actual name value is never written to the log. This produces an auditable record of additional names encountered during processing without exposing PII outside the local system.
4. Regex-based replacement for remaining PII types: ת.ז. (9-digit sequences), phone numbers, email addresses.

**Pass 2 (validation, before Cohere):** a verification stage that confirms de-identification succeeded. Three complementary checks are applied:
- **Regex scan:** scan for known PII patterns — 9-digit sequences (potential ת.ז.), email format, phone format.
- **Re-id map scan:** scan the output for any string that appears as a plaintext value in the re-identification map. A match indicates a token substitution was not applied.
- **NER re-scan:** run the Hebrew NER model a second time over the de-identified output. Any PER or ORG entity detected that does not have the form `PERSON_xxx` / `INST_xxx` is treated as a de-identification failure.

If any check fails: the document is blocked, an error entry is written to the system log (document path and failure type, but no PII values), and the document does not proceed to embedding.

### Re-ingest

- SQLite stores `file_hash` (SHA256 of file content) per source file.
- On re-ingest: skip files whose hash is unchanged. Re-process only changed files.
- Future feature: chunk-level diff for incremental `treatment_plan` updates.
- Generated clinic_visit_summary files: `file_hash` is stored in SQLite after serialization,
  so re-ingest skips them as unchanged. The hash must be computed from the final serialized bytes
  (after python-docx renders the document to a byte stream), not from the in-memory content
  before serialization.

---

## Step 4 — Indexing & Storage  ✅ LOCKED

**Decision: SQLite (local) + Pinecone (semantic search only).**

**SQLite** — local source of truth, never leaves premises:
- Stores all chunks with full metadata + de-identified text.
- Used for: all temporal queries, aggregations, exact lookups, "fetch all" without top_k limit.
- Re-indexing Pinecone from SQLite = trivial (no need to re-parse source files).

**Pinecone** — semantic search layer only:
- Receives only `treatment_goals` and `treatment_sessions` chunks. Family A sections (diagnosis,
  clinic_visit_summary) are never embedded — retrieved directly from SQLite (see Step 2).
- Stores embeddings (from Cohere, derived from de-identified text) + anonymous metadata.
- Used only when query requires semantic similarity (topic not in fixed taxonomy).
- Index strategy: one shared index. `patient_id` is a mandatory pre-filter on every query,
  applied before semantic search.
- Never receives raw PII. `patient_id` = `sha256(patient_folder_name)`.

**Re-identification map** — local file only:
`{ sha256(entity): original_value }` — covers all PII types (names, IDs, institutions, phones, emails).
Applied locally before showing answer to user. Full structure defined in Step 3.

### Modified-file re-ingest lifecycle

When the ingestion pipeline detects that a file has changed (stored hash ≠ current SHA256), it
replaces all existing records for that file without leaving stale or duplicate data. The procedure is:

1. **Collect prior Pinecone IDs:** query the relevant SQLite tables (`treatment_goals`,
   `treatment_sessions`) for all rows associated with this file and retrieve their `pinecone_id` values.
2. **Delete Pinecone vectors:** issue a batch delete against the Pinecone index for the collected IDs.
   This step is performed before modifying SQLite. If Pinecone deletion fails, the pipeline aborts
   and the existing SQLite rows are retained, preserving consistency between the two stores.
3. **Delete SQLite rows:** within a single transaction, delete all rows produced by this file from
   `family_a_sections`, `treatment_goals`, `treatment_sessions`, and `domain_findings`.
4. **Re-process the file:** run the file through the full ingestion pipeline (parse → de-identify →
   embed → store) as if it were new.
5. **Update `ingested_files`:** replace the existing row with the new `file_hash` and `ingested_at`.

The delete-before-insert pattern ensures that neither SQLite nor Pinecone can contain duplicate
records for the same source file at any point after a successful re-ingest cycle.

### Pinecone filtering strategy

**Decision: Metadata filter (Option A).**

Each vector stores `patient_id` in its metadata. At query time, a metadata equality filter
(`patient_id == <value>`) is applied before ANN search. All patients share one index.
Applying this filter on every Pinecone query is a mandatory design requirement, not an optional
guard. The patient volume is small (single clinic), so there is no performance or memory overhead
that would favour a namespace-based approach.

### Error Handling — Amendment (Step 9)

| Situation | Behaviour |
|---|---|
| SQLite write fails during ingestion | The transaction is rolled back. An `ERROR` entry is written to the log (file path only). The file is skipped; the pipeline continues to the next file. |
| Pinecone upsert fails after SQLite rows are already written | An `ERROR` entry is written to the log. The SQLite rows are retained (with `pinecone_id = NULL`). The row in `ingested_files` is **not written**, so the file will be re-processed on the next ingestion run. |

---

## Step 5 — Query Routing  ✅ LOCKED

**Decision: LLM extracts structured parameters → deterministic code routes.**

**Stage 1 — Patient selection (UI, local):**
The user selects a patient from a local list (dropdown/autocomplete) before submitting a query.
`patient_id = sha256(folder_name)` is resolved immediately at selection time, entirely in local
code. No patient name is sent to any external service at any point.

**Stage 2 — LLM extraction (small/fast model):**
The user's free-text question (Hebrew) is sent to the LLM without any patient name.
The LLM extracts and returns JSON:

```json
{
  "date_from":     "2026-01-01",
  "date_to":       null,
  "date_latest":   false,
  "template_type": "treatment_plan",
  "topic":         null,
  "intent":        "find_specific"
}
```

`template_type` values: `"diagnosis"` | `"clinic_visit_summary"` | `"treatment_plan"` | `null`.
`null` means the user did not specify a document type — handled per the routing table below.

- `date_from` / `date_to`: relative Hebrew dates ("חודשיים אחרונים") are resolved to absolute
  ISO 8601 by the LLM at extraction time (today's date injected into prompt).
- `date_latest: true`: for queries like "הטיפול האחרון" — routing code resolves via
  `SELECT MAX(session_date)` from SQLite; `date_from`/`date_to` are ignored.
- Cross-patient queries ("מי מהמטופלים...") are out of scope — system always operates on a
  single selected patient.

**Merge step (local code):**
Before the routing layer, local code merges the LLM output with the `patient_id` from Stage 1
into a single query object:

```json
{
  "patient_id":  "a3f7b2...",
  "date_from":   "2026-01-01",
  "date_to":     null,
  "date_latest": false,
  "topic":       null,
  "intent":      "find_specific"
}
```

All downstream retrieval code operates on this merged object. `patient_id` is always present;
`patient_name` never appears in the pipeline after the UI selection step.

**Stage 2 — Deterministic routing (code, not LLM):**

| Intent | template_type | Topic | Route |
|---|---|---|---|
| `summarize` | `treatment_plan` | any | `treatment_sessions` SQLite → LLM summarize |
| `summarize` | `diagnosis` / `clinic_visit_summary` | any | `family_a_sections` SQLite → LLM summarize |
| `summarize` | `null` | any | Error to user: *"ציני על איזה סוג מסמך את שואלת"* |
| `check_domain` | any | in 13-domain taxonomy | `domain_findings` SQLite exact lookup → LLM answer |
| `check_domain` | any | in taxonomy, result empty | Pinecone semantic + metadata filter → LLM answer |
| `check_domain` | any | NOT in taxonomy | Pinecone semantic + metadata filter → LLM answer |
| `find_specific` | any | open | Pinecone semantic + metadata filter → LLM answer |
| `compare_progress` | any | any | SQLite fetch sessions before/after `date_from` → LLM compare |

**Key principle:** LLM is good at language understanding (extraction); code is good at consistent
routing logic. The LLM never decides which retrieval strategy to use — only what parameters
the question contains. Code decides strategy.

### patient_id resolution

**Decision: patient selected in UI before query submission.**

`patient_id` is resolved locally at patient-selection time (UI dropdown/autocomplete), before any
query is submitted. No patient name enters the LLM extraction prompt or any external service.
The LLM extraction stage receives only the free-text question; it has no knowledge of patient
identity. `patient_id` is merged into the query object by local code after LLM extraction and
carried unchanged through all downstream retrieval stages.

### Error Handling — Amendment (Step 9)

| Situation | Behaviour |
|---|---|
| LLM extraction returns invalid JSON | One application-level retry with a clarified prompt. If the second attempt also produces invalid JSON, a user-facing message is returned: *"לא הצלחתי להבין את השאלה, נסי לנסח אחרת"*. No retrieval or generation call is made. |

---

## Step 6 — Retrieval Modes  ✅ LOCKED

**Guiding principle:** SQLite is the primary retrieval engine. Pinecone is used only when the
query topic is open/unknown and requires semantic similarity.

### Retrieval mode per intent

| Intent | Data source | Mechanism |
|---|---|---|
| `summarize` | `treatment_sessions` (SQLite) | patient_id + date filter → fetch ALL → LLM |
| `compare_progress` | `treatment_sessions` (SQLite) | patient_id + date filter → fetch ALL → LLM (different instruction) |
| `check_domain` (fixed domain) | `domain_findings` (SQLite) | patient_id + domain_name exact lookup → LLM |
| `check_domain` (fixed domain, empty result) | Pinecone | patient_id pre-filter + date pre-filter + semantic search, top_k=10 → LLM |
| `check_domain` (open domain) | Pinecone | patient_id pre-filter + date pre-filter + semantic search, top_k=10 → LLM |
| `find_specific` | Pinecone | patient_id pre-filter + date pre-filter + semantic search, top_k=10 → LLM |
| Family A — any intent | `family_a_sections` (SQLite) | patient_id + date filter → fetch ALL → LLM |
| `clinic_visit_summary` — any intent | `family_a_sections` (SQLite) | see date resolution table below |

**`clinic_visit_summary` date resolution:**

| `date_latest` | `date_from` | Behaviour |
|---|---|---|
| `true` | any | `SELECT MAX(session_date)` |
| `false` | not null | `WHERE session_date BETWEEN date_from AND date_to` |
| `false` | null | treated as `date_latest=true` (default to most recent) |

### Cross-cutting decisions

**Map-reduce:** Not required. Maximum ~20 sessions × 8 lines per session fits comfortably in a
single LLM context window. No chunked summarization needed.

**Date filter in Pinecone:** Applied as a metadata pre-filter (before semantic search), not
post-filtered in code. Date boundary is always hard (strict).

**top_k:** 10, uniform across all Pinecone queries. Search space is already narrow (one patient,
optional date window).

**Rerank:** Not used. Volume is too small to justify the added latency and API cost.

**Zero results from Pinecone:** Return a "not found" message to the user. No fallback to
SQLite fetch-all. Exact phrasing deferred to Step 7.

### compare_progress edge cases

The `compare_progress` intent fetches two sets of sessions from `treatment_sessions`: those before
the reference date and those after it. The following edge cases are handled in the retrieval layer
before the LLM is invoked:

| Situation | Expected behaviour |
|---|---|
| No sessions before the reference date | Return a user-facing message stating no prior sessions were found. Do not call the LLM with an empty "before" set. |
| No sessions after the reference date | Return a user-facing message stating no subsequent sessions were found. Do not call the LLM with an empty "after" set. |
| One side empty, the other non-empty | Return a partial-data message identifying which side is missing. Do not attempt a one-sided comparison. |
| No sessions at all for this patient | Return the standard "not found" message (same as the zero-results path). |

**Location of this logic:** the retrieval layer checks the result counts for both time windows
immediately after the SQLite fetch and before constructing the LLM prompt. The LLM is invoked
only when both sides contain at least one session. All edge-case responses are generated by code,
not by the LLM. Exact message phrasing is deferred to Step 7.

---

## Step 7 — Answer Generation & Re-identification  ✅ LOCKED

### Q&A pipeline

**LLM:** Claude Haiku 4.5, via LlamaIndex (model can be swapped in one line of config).

**Re-identification — after LLM:**
The LLM receives de-identified text with opaque tokens (`PERSON_a3f7...`). It never sees real
names — consistent with the privacy posture of Step 0.
The system prompt instructs the LLM: *"Treat PERSON_xxx tokens as natural person names and use
them fluently in the answer."*
After the LLM responds, code replaces every token with the real value from the local re-id map
before displaying the answer to the user.

**Answer format:** Free-text paragraph in Hebrew.

**Date format in answers:** Hebrew (e.g., "1 בינואר 2026") — decided in Step 2.

**Zero-results message (Hebrew):**
*"לא מצאתי מידע רלוונטי לשאלה זו. כדאי לנסח אחרת."*

### Generate clinic_visit_summary

**Trigger:** A dedicated button (outside the Q&A flow). Inputs: patient name + session date.

**Retrieval (all from SQLite):**
- Single session: `treatment_sessions` WHERE patient_id = ? AND session_date = ?
- Latest goals row: `treatment_goals` WHERE patient_id = ? AND session_date ≤ ? ORDER BY session_date DESC LIMIT 1
- Previous clinic_visit_summary: `family_a_sections` WHERE patient_id = ? AND template_type = 'clinic_visit_summary' ORDER BY session_date DESC LIMIT 1

**Generation:**
Claude Haiku 4.5 receives the three sources above and fills the Family A template structure
(same sections as diagnosis). Sections with no relevant content from the session are left blank.

**Re-identification before saving:**
- Document body: PERSON_xxx tokens replaced via re-id map.
- Header fields (not generated by LLM):
  - שם: PERSON_xxx token → re-id map
  - ת.ז.: pulled directly from `patient_metadata` WHERE field_name='national_id'
  - ת.ל.: pulled from `patient_metadata` WHERE field_name='date_of_birth' (dates are not tokenized per Step 3)

**File saved to patient folder:**
`clients/<patient_name>/סיכום ביקור <number> <name>.docx`
Patient folder name is resolved from `patient_id` via re-id map: `reid_map.reverse_lookup(patient_id)` (since `patient_id = sha256(folder_name)`, the map contains this entry).
Sequential number = scan patient folder for existing `סיכום ביקור` files + increment.
File contains real PII (re-identified). 

**SQLite record:**
De-identified version added to `family_a_sections` (template_type = 'clinic_visit_summary').
Row inserted into `ingested_files` after serialization with the known hash → re-ingest skips this file.
If the clinician edits the generated file, its hash changes and the file will be re-ingested on the next run through the full pipeline (including de-identification). This is the intended behavior and must not be treated as a special case.

### Error Handling — Amendment (Step 9)

| Situation | Behaviour |
|---|---|
| LLM returns an empty or malformed response | User-facing message: *"אירעה שגיאה בייצור התשובה, נסי שוב"*. |
| A `PERSON_xxx` / `ID_xxx` token appears in the LLM answer but is absent from the re-id map | The token is left visible in the displayed answer. A `WARNING` entry is written to the log containing the token ID only (not the original value). |
| Generate feature: no session found for the selected date | User-facing message returned before the LLM is called: *"לא נמצאה פגישה בתאריך זה"*. |
| Generate feature: writing the `.docx` file to the patient folder fails | An error message is returned to the user. The `family_a_sections` SQLite row is **not written** — file serialization and SQLite insertion are coupled; if one fails, neither is committed. |

---

## Step 8 — Evaluation & Testing  ✅ LOCKED

### Test data

Synthetic `.docx` files only — never real PHI in the test suite. Minimum set:
- 1 `diagnosis` file — all PII types present (name, ת.ז., institution, phone, email)
- 1 `clinic_visit_summary` file
- 1 `treatment_plan` file — ≥ 3 goals-table rows (Layer 1) + ≥ 5 session blocks (Layer 2)

### Test components

**1 — De-identification (zero-tolerance)**
A manifest lists every PII entity (type + value) in every synthetic file.
After de-id, assert that zero known PII entities remain in the output text.
Also: Pass 2 (validation) is tested independently — separate test cases exercise each of the three
checks (regex scan, re-id map scan, NER re-scan) with known-bad input designed to trigger each check.
Additionally: synthetic files that include a third-party person name (not the patient's own name) are
processed and the system log is asserted to contain a `WARNING` entry for that entity.

**2 — Query routing & SQL (unit)**
- Hebrew relative dates → ISO 8601 (multiple examples: "חודשיים אחרונים", "שבוע שעבר", etc.)
- Intent classification: correct intent returned for each question type
- Routing decision: correct retrieval path selected per intent
- SQL queries: correct rows returned for given patient_id + date range combinations

**3 — Answer quality (E2E)**
- **Ground-truth set:** 15 questions created manually by the user, over the synthetic data, with known correct answers.
- **LLM-as-judge:** Claude Haiku 4.5 evaluates each answer on 4 criteria (scale 1–5):
  - `faithfulness` — answer contains no information absent from the retrieved chunks
  - `completeness` — answer addresses the question
  - `date_accuracy` — all dates mentioned are correct
  - `hebrew_fluency` — answer is clear, natural Hebrew
- Results saved to JSON + printed as a console report after each eval run.
- **Config toggle:** `config.yaml` → `evaluation.enabled: true/false`. When `false`, the eval module is skipped entirely.

**4 — Generate feature (smoke test, E2E)**
- Input: patient name + session date → assert output `.docx` file is created in the patient folder.
- Assert `family_a_sections` contains a new de-identified row for the generated summary.
- Assert `ingested_files` contains a row with the correct file path and hash.
- Assert re-identification was applied correctly in the output file body (no PERSON_xxx or ID_xxx tokens remain).

**5 — CI**
Not implemented now. Documented here as a future option: unit tests (components 1 and 2) are CI-safe; E2E tests (3 and 4) require API keys and a local SQLite instance.

---

## Prompt Engineering Guidelines

This section defines the principles and standards that all prompt templates throughout the system
must follow. It is an architecture guide — not a collection of prompts. Individual prompts are
written during implementation of each step; this section governs how they are written.

### Determinism

Every prompt must produce predictable output given the same inputs. Avoid open-ended instructions
that invite the model to improvise. Use explicit, constrained directives ("return only valid JSON",
"answer only from the text below", "do not infer or extend beyond what is stated"). Where the
output format matters (JSON extraction, structured routing parameters), define the schema in the
prompt and assert format compliance in the calling code.

**Temperature:** Set `temperature=0` for all extraction prompts (Stage 2 query parameter
extraction, any prompt whose output is parsed as structured data). For answer-generation prompts
(Step 7 Q&A and generate feature), a low non-zero temperature (e.g. `0.2`) is acceptable.
These two classes of prompts must never share a temperature setting.

### Grounding — context-only answers

Every retrieval prompt must include an explicit instruction requiring the model to base its answer
solely on the retrieved context provided in the prompt. If the retrieved context is insufficient,
the model must say so rather than infer. This applies without exception to clinical content.
Hallucinated clinical content is a patient-safety issue in this domain.

### Anti-hallucination

Never ask the model to generate clinical conclusions beyond what the retrieved text explicitly
states. Instruct the model to quote or paraphrase from the source text and to acknowledge gaps
explicitly. Do not include instructions that invite elaboration, inference, or "professional
judgement" from the model.

### Separation of instructions and data

Prompt text (instructions, format requirements, and constraints) must be held in a separate
template variable from dynamic data (retrieved chunks, patient context, query text). This
separation prevents prompt injection from document content, makes the instruction layer
independently testable, and allows dynamic data to change without touching the instruction logic.

### Prompt versioning

Each prompt template carries a version identifier in a comment or metadata field (e.g.,
`# prompt: extraction_v2`). When a prompt is changed, the version is incremented. Evaluation
results (Step 8) are recorded against the prompt version that produced them. This makes
regressions traceable and enables comparison between prompt versions.

### Centralization

All prompt templates are stored in a single designated module (e.g., `prompts.py` or a
`prompts/` package). No prompt string is constructed inline in business logic. This ensures every
prompt used in the system can be found, reviewed, and updated in one place without grepping across
the codebase.

### Consistency across intents

Prompts for different intents (`summarize`, `find_specific`, `compare_progress`, etc.) must share
a common structural pattern: system instruction block → retrieved context → question. Deviations
from this pattern require explicit justification. Consistent structure reduces the risk of
intent-specific edge cases being handled differently by the model.

### Maintainability

Prompt templates must be readable without running the system. Avoid constructing prompts
programmatically through concatenation of many small fragments. A maintainer must be able to read
a prompt template and understand its complete behaviour without tracing through multiple code paths.

### Future extensibility

When adding a new intent or template type, the existing prompt structure should accommodate the
new case as an additive change, not a restructuring of unrelated prompts. Design prompt templates
to accept new context fields as optional additions. New intents follow the same centralization,
versioning, and structural conventions defined here.

When prompts include anonymization tokens (e.g., PERSON_xxx, INST_xxx), the prompt must explicitly instruct the model to treat these tokens as normal semantic entities rather than as code or placeholders.

---

## Step 9 — Code Architecture, Runtime, Ops, Logging, Error Handling  ✅ LOCKED

### File & Directory Structure

```
clinical-rag/
├── README.md                     # project description, requirements, setup, run instructions
├── .env.example                  # committed to git — template with placeholder values
├── .env                          # gitignored — real API keys only
├── config.yaml                   # committed to git — all non-secret config
├── setup.bat                     # first-time setup (uv sync + DB init + ONNX conversion)
├── start.bat                     # daily launch script
├── pyproject.toml                # uv project definition
├── uv.lock
├── .gitignore
│
├── app/
│   ├── __init__.py
│   ├── main.py                   # entry point — starts Gradio server
│   ├── setup.py                  # called by setup.bat: DB schema init + ONNX conversion
│   ├── config.py                 # loads config.yaml + .env → typed dataclass
│   ├── logging_setup.py          # TimedRotatingFileHandler — daily rotation, 7-day retention
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── adapter_a.py          # Family A parser (diagnosis, clinic_visit_summary)
│   │   ├── adapter_b.py          # Family B parser (treatment_plan: L1 + L2)
│   │   └── pipeline.py           # Ingestion Workflow (LlamaIndex Steps)
│   │
│   ├── deidentification/
│   │   ├── __init__.py
│   │   ├── deid.py               # Pass 1 orchestrator
│   │   ├── ner.py                # ONNX NER singleton + extract_entities()
│   │   ├── validation.py         # Pass 2 gate (3 verification checks)
│   │   └── reid_map.py           # load / save / add_entity / reidentify_text
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py                 # SQLite: schema init + all CRUD
│   │   ├── pinecone_client.py    # Pinecone: init, upsert, query, delete
│   │   └── models.py             # shared dataclasses across all layers
│   │
│   ├── query/
│   │   ├── __init__.py
│   │   ├── extractor.py          # LLM extraction: Hebrew NL → JSON params
│   │   ├── router.py             # intent → retrieval path (deterministic, no LLM)
│   │   └── retrieval.py          # SQLite + Pinecone fetch + Pinecone offline fallback
│   │
│   ├── generation/
│   │   ├── __init__.py
│   │   ├── qa.py                 # Query Workflow (LlamaIndex Steps)
│   │   └── summary_generator.py  # Generate Summary Workflow (LlamaIndex Steps)
│   │
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── extraction.py         # extraction prompt templates (temperature=0)
│   │   ├── qa.py                 # Q&A prompt templates, one per intent (temperature=0.2)
│   │   └── generation.py         # generate-summary prompt templates (temperature=0.2)
│   │
│   └── ui/
│       ├── __init__.py
│       └── gradio_app.py         # Gradio Blocks — detail in Step 10
│
├── evaluation/
│   ├── __init__.py
│   ├── runner.py
│   ├── judge.py
│   └── ground_truth/
│       └── questions.json
│
├── tests/
│   ├── synthetic_data/
│   ├── test_deid.py
│   ├── test_routing.py
│   └── test_generate.py
│
├── models/
│   └── heBERT_NER_onnx/          # gitignored — generated by setup.bat
│
├── data/                         # gitignored — generated at runtime
│   ├── .gitkeep
│   ├── clinical_rag.db
│   └── reid_map.json
│
└── logs/                         # gitignored — generated at runtime
    └── .gitkeep
```

### Key Module Contracts

**`storage/models.py`** — shared dataclasses (no ORM):
```python
FamilyAChunk(patient_id, template_type, session_date, section, text_deidentified)
TreatmentGoalsChunk(patient_id, session_date, goals_text_deidentified, pinecone_id)
TreatmentSessionChunk(patient_id, session_date, session_text_deidentified, pinecone_id)
DomainFinding(patient_id, session_date, domain_name, domain_text_deidentified)
QueryParams(patient_id, date_from, date_to, date_latest, template_type, topic, intent)
RetrievalResult(chunks: list[str], source_table: str, count: int)
```

**`deidentification/validation.py`** — Pass 2 gate:
```python
validate_deidentified(text: str, reid_map: dict) -> ValidationResult
  # _check_regex_patterns(text)       — 9-digit sequences, email, phone formats
  # _check_reid_map_values(text, map) — no plaintext value from map present in text
  # _check_ner_rescan(text)           — no PER/ORG entity without PERSON_/INST_ prefix
```

**`deidentification/reid_map.py`** — re-id map operations:
```python
load(path) -> dict
save(path, map: dict) -> None
add_entity(map, entity_type, value) -> str     # returns token e.g. PERSON_a3f7...
reverse_lookup(map, token) -> str | None
reidentify_text(map, text) -> str              # replaces all tokens in text
```

### LlamaIndex Workflows — Steps & Event Architecture

Each step is decorated with `@step`, receives one typed Event, and emits one typed Event.
LlamaIndex resolves the execution graph automatically from the Event types.

**Ingestion Workflow** (`app/ingestion/pipeline.py`):
```
IngestStartEvent(file_path)
  → [ParseStep]          → ParsedEvent(chunks, template_type)
  → [DeidentifyStep]     → DeidentifiedEvent(chunks)
                           | BlockedEvent(reason) → [LogAndStopStep] → StopEvent
  → [ValidateStep]       → FamilyAStoreEvent(chunks)
                           | TreatmentEmbedEvent(chunks)
  → [StoreFamilyAStep]   → StopEvent                          (Family A path)
                           -- also UPSERT to patient_metadata: date_of_birth, hmo_name, tz_token
  → [EmbedStep]          → EmbeddedEvent(chunks)              (treatment_plan path)
  → [StoreTreatmentStep] → StopEvent
```

**Query Workflow** (`app/generation/qa.py`):
```
QueryStartEvent(question, patient_id)
  → [ExtractStep]    → QueryParamsEvent(date_from, date_to, date_latest, topic, intent, patient_id)
  → [RouteStep]      → RouteDecisionEvent(strategy, params)
  → [RetrieveStep]   → RetrievalResultEvent(chunks)
                       | EmptyResultEvent → StopEvent("לא מצאתי...")
  → [GenerateStep]   → TokenizedAnswerEvent(answer_with_tokens)
  → [ReidentifyStep] → StopEvent(final_answer)
```

**Generate Summary Workflow** (`app/generation/summary_generator.py`):
```
GenerateStartEvent(patient_id, session_date)
  → [FetchSourcesStep]    → SourcesEvent(session_text, goals_text, prev_summary_text)
  → [GenerateSummaryStep] → TokenizedDocEvent(sections_dict)
  → [BuildDocStep]        → ReidentifiedDocEvent(sections_dict)
  → [SaveDocStep]         → StopEvent(file_path)
```

### UI Type & Runtime

**UI:** Gradio — persistent server. Detail deferred to Step 10.
**Launch:** `start.bat` → `uv run python -m app.main` → available at `http://localhost:7860`.
**NER model:** loaded once at ingestion startup as a module-level singleton in `ner.py`.
Not loaded during query-only usage.

### Ops — Setup (First Run Only)

`setup.bat` runs three steps in sequence:

```bat
@echo off
echo [1/3] Installing dependencies...
uv sync
echo [2/3] Initializing database...
uv run python -m app.setup db
echo [3/3] Converting NER model to ONNX (this takes a few minutes)...
uv run python -m app.setup ner
echo Setup complete. Run start.bat to launch the application.
pause
```

`start.bat` (daily use):

```bat
@echo off
uv run python -m app.main
```

### Logging

- **Single log file** for all event types: operational INFO, de-id WARNINGs (Step 3), ERRORs
- **Daily rotation:** new file at midnight; files older than 7 days are deleted automatically
  (`TimedRotatingFileHandler(when='midnight', backupCount=7)`)
- **Default level:** `INFO`
- **What is logged at INFO:** query received, intent identified, chunk count retrieved,
  LLM call initiated, answer displayed, file ingestion completed, SQLite row written,
  generate feature triggered
- **Local only.** Log files never leave the machine. No PII is ever written to logs.

### Error Handling

| Situation | Behaviour |
|---|---|
| Cohere / Pinecone / Claude transient HTTP error | SDK-level retry (built into each SDK). No additional retry layer. |
| Pinecone query unavailable | Fallback to SQLite fetch-all for that query; WARNING in log and UI |
| Cohere embed unavailable during ingestion | File skipped; ERROR in log; file not marked as ingested |
| Claude unavailable | User-facing error message; no fallback |
| re-id map missing — Q&A | Q&A continues; UI warning: *"לא ניתן להציג שמות — מפת הזיהוי חסרה"* |
| re-id map missing — Generate | Generate blocked; user-facing error |

Step-specific error handling is documented as amendments in the relevant locked steps
(Steps 2, 4, 5, 7).

### Libraries

| Library | Role |
|---|---|
| `llama-index-core` | Workflow orchestration (Steps + Events) |
| `llama-index-llms-anthropic` | Claude Haiku via LlamaIndex |
| `llama-index-embeddings-cohere` | Cohere embeddings via LlamaIndex |
| `anthropic` | Anthropic SDK (required by llama-index-llms-anthropic) |
| `cohere` | Cohere SDK (required by llama-index-embeddings-cohere) |
| `pinecone` | Pinecone SDK v3+ (direct calls, not via LlamaIndex) |
| `python-docx` | `.docx` file parsing |
| `transformers` | Tokenizer for NER (required even with ONNX runtime) |
| `onnxruntime` | NER inference — replaces torch entirely |
| `optimum` | One-time heBERT_NER → ONNX conversion (run in `setup.bat` only) |
| `gradio` | UI framework |
| `pyyaml` | `config.yaml` loading |
| `python-dotenv` | `.env` loading |
| `pytest` | Testing |
| `json`, `hashlib`, `sqlite3`, `re`, `datetime`, `logging` | stdlib — no external dependency |

**Explicitly excluded:** `torch`, `tenacity`, `llama-index` (old monolith package)

### NER — ONNX Rationale

`heBERT_NER` (Hebrew NER, HuggingFace) is the chosen model. It is converted from PyTorch
format to ONNX format once during `setup.bat`. At runtime, `onnxruntime` executes the model
without requiring `torch`. The model weights are identical; only the inference engine changes.
Result: ~200 MB RAM during ingestion (vs ~500 MB with torch), no torch install (~250 MB saved).
Quality is identical to the original torch-based model.

### config.yaml (committed to git — no secrets)

```yaml
patients_root: "C:/SpeechTherapy/clients"
data_dir: "./data"
logs_dir: "./logs"
models_dir: "./models"

pinecone:
  index_name: "clinical-rag"
  dimension: 1024

cohere:
  model: "embed-multilingual-v3.0"

anthropic:
  extraction_model: "claude-haiku-4-5-20251001"
  generation_model: "claude-haiku-4-5-20251001"
  temperature_extraction: 0
  temperature_generation: 0.2

logging:
  level: "INFO"
  retention_days: 7

evaluation:
  enabled: false
```

---

## Step 10 — UI Design (Gradio)  ✅ LOCKED

### App Title

`"מערכת שאלות ותשובות — תיקי מטופלים"`

### Overall Layout

**Global element (above all tabs):**
- App title
- Patient dropdown — global, applies to both tabs; populated from SQLite (`patient_metadata`) on app start
- `"רענן רשימה"` button — reloads patient list from filesystem/SQLite on demand (no server restart)

**2 Tabs:**

| Tab | Label | Contents |
|-----|-------|----------|
| 1 | 💬 שאלות ותשובות | Question input + Submit button + Answer display |
| 2 | 📝 יצירת סיכום ביקור | Session date dropdown + Generate button + Status message |

Ingestion is command-line only (`setup.bat` / manual re-run). No ingestion tab in the UI.

### Tab 1 — שאלות ותשובות (Q&A)

- Hebrew text input (question)
- Submit button
- Read-only answer display area
- Complete response — no streaming
- No conversation history — each question is independent; previous answer is replaced
- **Future feature (not implemented now):** chat-style Q&A with in-session history

### Tab 2 — יצירת סיכום ביקור (Generate)

- Session date dropdown — populated dynamically when patient is selected (queries
  `treatment_sessions` WHERE `patient_id` = selected patient, ORDER BY `session_date` DESC)
- Changing the selected patient triggers an automatic update of the date dropdown
- `"צור סיכום"` button → triggers Generate Summary Workflow → saves `.docx` to patient folder
- Status display: file path on success; user-facing Hebrew error message on failure
- No preview or confirmation step — file is saved directly (per Step 7 decision)

### Patient Selection

- Simple dropdown (not autocomplete)
- Populated from SQLite (`patient_metadata`) on app start — only patients with ingested data are shown
- Manual refresh via `"רענן רשימה"` button
- Changing the selected patient: clears current answer (Tab 1) and resets date dropdown (Tab 2)

### RTL

~15–20 lines of custom CSS: `direction: rtl` on the main Gradio container, alignment fixes for
tabs and labels. Applied via `gr.Blocks(css=custom_css)`.

### Color Palette & Theme

| Element | Value |
|---------|-------|
| Gradio base theme | `Soft` |
| Background | `#FFFFFF` / `#F8F9FA` |
| Primary accent | `#5B9BB5` (soft blue-teal) |
| Text | `#2D3748` |
| Borders / subtle | `#E2E8F0` |

Icons: emoji in tab labels and section headers (💬 📝 👤).
Context: speech-language pathology (paramedical) — no medical icons (syringes, hearts, etc.).

### Implementation module

`app/ui/gradio_app.py` — Gradio Blocks. Entry point: `app/main.py`.

---

### .gitignore

```gitignore
# Secrets
.env

# Data (PHI + local state)
data/*
!data/.gitkeep

# Logs
logs/*
!logs/.gitkeep

# NER model (generated by setup)
models/

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/

# Virtual environment
.venv/

# Testing
.pytest_cache/
.coverage
htmlcov/

# IDE
.vscode/settings.json
.idea/

# OS
.DS_Store
Thumbs.db
```
