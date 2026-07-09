# Session 3 — Answer Quality & Routing Review (Track C)

> Read `.claude/plans/SESSIONS-OVERVIEW.md` first for working rules.
> Conversation in Hebrew. No file changes before approval. Show before/after for every edit.

## Goal

Fix the three concrete answer-quality bugs, then hold a structured product review of the entire
question-routing table with the user and align code + tests + PLAN.md to whatever is decided.

## Part 1 — Concrete fixes

### C1 — Dates are missing from the context sent to the LLM (user-reported bug)

**Symptom:** asking "מה עשינו בחודשיים האחרונים?" returns "המידע אינו מציין תאריכים...".
**Root cause:** `adapter_b.py` strips the date line when splitting Layer-2 session blocks (the date
becomes only the `session_date` column). Context assembly then joins bare texts:
`retrieval.py` returns `chunks` as plain strings and `qa.py::generate_step` joins them with
`\n---\n` — no date ever reaches the LLM, which then (correctly) refuses date questions.
**Fix direction:** prefix every chunk with its date when building context, e.g.
`[טיפול מתאריך 2025-03-07]\n<text>`. Touch points: all paths in `app/query/retrieval.py`
(`_fetch_treatment_sessions`, `_fetch_compare` before/after sets, `_pinecone_retrieve` resolved
texts, `_sqlite_fallback`, goals rows) and `family_a_sections` (prefix section name + document
date). Decide format with the user (ISO vs Hebrew date — prompts instruct the LLM to output Hebrew
dates either way). This likely means `RetrievalResult.chunks` carries (date, text) pairs or
pre-formatted strings — choose the simpler.

### C2 — "Latest document" Q&A fetches one section out of five

`retrieval.py::_fetch_family_a` (the `session_limit is not None or date_from is None` branch) calls
`db.fetch_latest_family_a`, which returns a **single row** (`LIMIT 1` = one arbitrary section), so
answers about "the diagnosis" are based on ~1/5 of the document. Fix: use
`db.fetch_latest_family_a_as_dict` (all sections of the latest date) and pass section-labeled texts.

### C3 — Q&A answers still truncated at 512 tokens

`app/generation/qa.py::QueryWorkflow.__init__` creates `LlamaAnthropic` without `max_tokens`
(default 512). Long answers get cut mid-sentence. Align with the generation-side decision from
Session 2 (config key recommended, e.g. `anthropic.max_tokens_generation`).

## Part 2 — Routing review (discussion first, code after)

The user asked to re-explain and re-discuss how every question is routed. Walk through this
table (current behavior of `app/query/extractor.py` → `app/query/router.py` →
`app/query/retrieval.py`) with Hebrew examples, then collect decisions:

| Intent extracted | Condition | Current route |
|---|---|---|
| summarize | diagnosis / clinic_visit_summary | SQLite `family_a_sections` |
| summarize | treatment_plan **or no type** | SQLite `treatment_sessions` (date-filtered fetch-all) |
| compare_progress | any | SQLite sessions before/after reference date |
| check_domain | topic ∈ 13 fixed domains | SQLite `domain_findings` exact (then parent-domain, then Pinecone fallback) |
| check_domain | open topic | Pinecone semantic |
| find_specific | family A type | SQLite `family_a_sections` |
| find_specific | no topic | SQLite `treatment_sessions` |
| find_specific | with topic | Pinecone semantic |
| anything else | — | Pinecone semantic |

Pinecone mechanics to explain to the user (she asked what is stored there): only de-identified
`treatment_goals` rows and `treatment_sessions` blocks are embedded (Cohere multilingual). Vector
metadata: `patient_id` (sha256 hash), `session_date`, `session_date_num`, `chunk_type`. **No text
and no names are stored in Pinecone** — matches only return ids+metadata, and the actual text is
fetched back from local SQLite. Every query is pre-filtered by patient and optional date range,
top_k=10. If Pinecone is down → graceful SQLite fetch-all fallback.

Decision points to put to the user (with trade-offs, per the working rules):
1. `summarize` with no document type: current = assume treatment sessions; original PLAN = ask the
   user to specify. Which behavior?
2. `find_specific` with no topic: current = SQLite fetch-all; original PLAN = Pinecone. (Current is
   arguably more sensible — nothing to embed — but confirm.)
3. Are the extraction examples/prompt (`app/prompts/extraction.py`) producing the right intents for
   her real question styles? Review with 5–10 real questions from her.
4. Whatever is decided → update the 2 failing tests in `tests/test_routing.py`
   (`test_route_summarize_null_template`, `test_route_find_specific`) to encode the decided
   behavior, and the routing tables in PLAN.md Steps 5–6.

## Definition of done

- C1–C3 approved, applied, and verified end-to-end (ask a date-ranged question and see correct
  dated answer; ask about the latest diagnosis and see all sections reflected).
- Routing decisions locked, code aligned, `tests/test_routing.py` fully green (`uv run pytest`).
- PLAN.md Steps 5–6 updated and approved. Update `SESSIONS-OVERVIEW.md` status table.
