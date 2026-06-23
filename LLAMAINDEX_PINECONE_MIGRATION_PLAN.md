# LlamaIndex + Pinecone Migration — Implementation Plan

## Overview
The project currently uses LangChain as its RAG framework with FAISS as a local vector store.
This plan migrates the full stack to LlamaIndex + Pinecone while keeping all domain logic intact:
the template-aware Hebrew document chunker, PII anonymizer, and Gradio UI are not changing.
The public API of `RAGPipeline` (`search`, `ask`, `reload`) is preserved so `app.py` requires
only minimal edits.

## Decisions

**Framework:** LlamaIndex — required by course assignment; replaces all LangChain abstractions.

**Vector Store:** Pinecone — replaces local FAISS. Index must be created manually in Pinecone
Console before running ingest (dimension=1024, metric=cosine, to match Cohere multilingual v3.0).

**Embeddings:** Cohere `embed-multilingual-v3.0` via LlamaIndex `BaseEmbedding` — same model and
dual input_type logic (search_document / search_query) as today, only the ABC changes.

**Incremental Indexing:** LlamaIndex `IngestionPipeline` + `SimpleDocumentStore` — replaces
`SQLRecordManager`. The docstore cache is persisted locally under `storage/pipeline_cache/`.
On repeat runs, unchanged nodes are skipped automatically. File deletion from Pinecone is deferred.

**Chunker Interface:** `DocxTemplateNodeParser(BaseNodeParser)` wraps the existing `Chunker` —
LlamaIndex passes Documents with `file_path` in metadata; the parser calls `Chunker.chunk_file()`
and returns `TextNode` objects. No chunking logic changes.

**PII Filter:** Converted to a standalone `anonymize_text(text: str) -> str` function —
no framework dependency. Same regex patterns, applied to node text before ingestion.

**Node Postprocessor:** Deferred — not part of this migration step.

## Files Changed

| File | Current state | What changes |
|---|---|---|
| `requirements.txt` | LangChain + FAISS packages | Remove all LangChain/FAISS; add LlamaIndex + Pinecone packages |
| `src/pii_filter.py` | Class inheriting `BaseDocumentTransformer` (LangChain) | Replace with standalone `anonymize_text(str) -> str` function; regex logic unchanged |
| `src/embeddings.py` | `CohereEmbedder(Embeddings)` — LangChain ABC | Change parent to `BaseEmbedding` (LlamaIndex); rename methods to match LlamaIndex interface; logic unchanged |
| `src/chunker.py` | Has `Chunk.to_lc_doc()` and `DocxTemplateLoader` (LangChain) | Delete `to_lc_doc()` and `DocxTemplateLoader`; add `Chunk.to_llama_node()` with deterministic ID; add new `DocxTemplateNodeParser(BaseNodeParser)` class |
| `ingest.py` | LangChain FAISS + `SQLRecordManager` + `index()` | Full rewrite: `SimpleDirectoryReader` → `DocxTemplateNodeParser` → `anonymize_text` → `IngestionPipeline` → Pinecone |
| `src/pipeline.py` | LangChain `ChatAnthropic` + FAISS + `StrOutputParser` | Full rewrite: `VectorIndexRetriever` + `ResponseSynthesizer`; same public method signatures |
| `app.py` | Loads `COHERE_API_KEY` + `ANTHROPIC_API_KEY` | Add `PINECONE_API_KEY` + `PINECONE_INDEX_NAME` to env loading; `reload()` re-inits from Pinecone; UI unchanged |

## New Dependencies

| Package | Version | Why needed |
|---|---|---|
| `llama-index-core` | >=0.11 | Core LlamaIndex framework |
| `llama-index-embeddings-cohere` | latest | Cohere embeddings via LlamaIndex |
| `llama-index-vector-stores-pinecone` | latest | Pinecone vector store integration |
| `llama-index-llms-anthropic` | latest | Claude LLM via LlamaIndex |
| `pinecone-client` | >=3.0 | Pinecone Python SDK |

## Implementation Order

1. `requirements.txt` — swap dependencies; run `pip install -r requirements.txt`
2. `src/pii_filter.py` — convert to standalone function; most isolated change
3. `src/embeddings.py` — swap ABC; verify Cohere calls still work
4. `src/chunker.py` — add `to_llama_node()`, add `DocxTemplateNodeParser`, remove LangChain parts
5. `ingest.py` — full rewrite using components from steps 1–4
6. `src/pipeline.py` — full rewrite; keep `search`/`ask`/`reload` signatures identical
7. `app.py` — add new env vars, update `reload()`, verify UI still runs

## Pre-requisite Setup (before running code)

1. Create a Pinecone account and a new index with:
   - **Dimensions:** 1024
   - **Metric:** cosine
2. Add to `.env`:
   ```
   PINECONE_API_KEY=<your key>
   PINECONE_INDEX_NAME=<your index name>
   ```

## Verification

```bash
pip install -r requirements.txt
python ingest.py                  # should report chunks uploaded to Pinecone
python app.py                     # open http://localhost:7860
# Ask: "מה הממצאים העיקריים?" — expect answer + sources from Pinecone
```

## Deferred Questions

**Deletion of removed files from Pinecone:** When a `.docx` file is removed from the folder,
its vectors remain in Pinecone. A cleanup mechanism (e.g. comparing stored IDs against current
file list) is out of scope for this step.

**Node Postprocessor / Reranker:** Explicitly excluded from this migration step.
