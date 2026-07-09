# Session 4 — Ops, Logging & PLAN Consolidation (Track D)

> Read `.claude/plans/SESSIONS-OVERVIEW.md` first for working rules.
> Conversation in Hebrew. No file changes before approval. Show before/after for every edit.
> Best run **after** sessions 1–3 so the PLAN.md rewrite captures everything.

## Tasks

### D1 — Rename the Pinecone index to `clinical-rag`

Current `config.yaml` has `index_name: "rag-index"`; the intended name (PLAN.md) is
`clinical-rag`. Renaming is not an in-place operation — plan:
1. Create the `clinical-rag` index (dimension 1024, metric cosine — match Cohere
   embed-multilingual-v3.0).
2. Update `config.yaml`.
3. Re-populate: check `scripts/reset_and_reingest.py` (exists, ~80 lines — read it) — vectors can
   be rebuilt from SQLite or by full re-ingest. Since Session 1 fixed the NER gate, a **full
   re-ingest from source files is preferred** (older vectors were embedded without NER protection —
   re-embedding cleans any leaked third-party names inside Pinecone).
4. Delete the old `rag-index` index (destructive — get explicit confirmation, and only after the
   new index is verified with a real query).

### D2 — Project-wide structured logging

**Locked policy change (user decision, 2026-07-09):** PII in local logs is acceptable. This
overrides PLAN.md Step 9's "No PII is ever written to logs" — the PLAN text must be rewritten to
say logs are local-only and may contain PII (and therefore live under the same protection as
`data/`).

Requirements from the user:
- Log, for every Q&A call: the user question, the **exact context** sent to the LLM, and the answer
  (log the tokenized answer and/or final answer — with PII allowed, log the final one; confirm).
- Same for the generate-summary flow (prompt sources + raw LLM output — raw output is also the
  debugging tool for parse failures, see Session 2 B4).
- General INFO coverage across the pipeline (ingestion per-file results already exist).

Design points: a small helper (e.g. `log_llm_call(kind, prompt, response)`) to keep call sites
clean; consider a dedicated `llm_calls.log` file vs the single rotating log (single file is the
current PLAN decision — ask); log level for full prompts (INFO vs DEBUG + config toggle — full
prompts are large; recommend DEBUG with `logging.level` already configurable, or a dedicated
`logging.log_prompts: true/false` key).

### D3 — start.bat / setup.bat warning cleanup

- `warning: Failed to hardlink files...` — uv cache and project are on different drives (C: vs D:).
  Fix: add `set UV_LINK_MODE=copy` at the top of `start.bat` and `setup.bat` (cosmetic, harmless).
- `StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated` — comes from
  gradio's own code; the filter already present in `app/main.py:2` doesn't catch it (category
  mismatch). Options: broaden the `warnings.filterwarnings` to the actual category / message, or
  upgrade gradio to a version that fixed the call (`uv lock --upgrade-package gradio` — check
  changelog first). Harmless either way; pick the cheapest.

### D4 — Full PLAN.md rewrite (English)

Rewrite PLAN.md to match reality after sessions 1–3. Known drift to fold in (verify each against
code at session time):

- Step 3/9: `run_mode` production/test + fail-closed NER gate; logging policy change (PII allowed).
- Step 7: entire generate-summary redesign (base-document copy architecture, domain-findings merge,
  treatment-oriented headings, date-range input instead of single session date, no PDF, max_tokens).
- Steps 5–6: routing table as decided in Session 3; dated context format; latest-document fetch.
- Step 9/10: UI has 4 tabs (Q&A, generate, **ingestion from UI**, patient-files open-in-Word);
  server binds to 127.0.0.1; `nest_asyncio`; `display_name` field in `patient_metadata`.
- Step 2/4: Pinecone metadata includes `session_date_num` (numeric date filtering); index name
  `clinical-rag`; `parent_domain` column in `domain_findings` + parent-domain routing.
- Step 1: diagnosis filename prefix is `סיכום אבחון` (fix the `אבחון בנים/בנות` claim and the
  `detect_template_type` docstring in `app/ingestion/adapter_a.py`).
- Libraries table: no `optimum`… actually verify final dependency set from `pyproject.toml`
  (torch/optimum in `setup` extra, reportlab/bidi removed, nest-asyncio added).
- Keep the locked-step structure; mark amended sections with a dated amendment note.

Get approval on an outline of the PLAN.md changes before rewriting, then section by section.

### D5 — Batch Cohere embedding calls during ingestion (added 2026-07-09, user-approved)

**Current state:** `embed_step` in `app/ingestion/pipeline.py` calls
`get_cohere_embedding()` once **per chunk** (every goals row and every session block = a separate
HTTP call), and `app/storage/pinecone_client.py::get_cohere_embedding` creates a **new
`cohere.ClientV2` on every call**. A treatment plan with 25 chunks = 25 network round-trips.
Cohere's embed API accepts up to **96 texts per call**.

**Fix:**
1. Add `get_cohere_embeddings_batch(texts: list[str], api_key, model) -> list[list[float]]` in
   `pinecone_client.py` — single `co.embed(texts=[...])` call, auto-splitting into chunks of 96
   if the list is longer, preserving order.
2. Rework `embed_step` to collect all goals + session texts for the file, embed them in one batch,
   and distribute the returned vectors back to the per-chunk dicts.
3. Reuse a single Cohere client per ingestion run instead of one per call.

**Acceptance:** ingesting a multi-session treatment plan produces exactly
`ceil(n_chunks / 96)` Cohere calls (verify via log), with otherwise identical stored
vectors/behavior. This touches the same `embed_step` code as Session 1's NER work — re-read the
current pipeline before implementing.

## Definition of done

- New index live and answering queries; old index deleted after verification.
- Logging implemented per approved design; sample log shown to the user.
- Clean `start.bat` console output.
- Embedding calls batched (D5) — verified via ingestion log.
- PLAN.md fully consistent with the codebase; approved by the user.
- `SESSIONS-OVERVIEW.md` status table updated — roadmap complete.
