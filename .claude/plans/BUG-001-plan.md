# Bug Fix Plan: BUG-001

## Problem Analysis

**Root cause:** `Chunk.to_llama_node()` in `src/chunker.py` (lines 28–32) computes the node ID solely from `source` (absolute file path) + `chunk_index`. Text content is excluded from the hash. This means that when a `.docx` file is modified:

1. The `Chunk` objects produced by the new ingest have the exact same `node_id` values as the previous run.
2. LlamaIndex's `IngestionPipeline` uses `SimpleDocumentStore` as a deduplication cache. Before embedding or uploading, it checks whether each node's `id_` already exists in the docstore. If it does, the node is skipped entirely.
3. The stale vector in Pinecone (from the previous, now-outdated content) is never replaced.
4. Queries against the updated file therefore return outdated answers from Pinecone.

**Why content hash alone is not enough (orphan problem):** If we only add a content hash to the node ID, changed nodes will no longer be skipped by the docstore — they will be inserted as new records. However, the **old records** (with the original ID, now hash-mismatched) remain in Pinecone indefinitely as orphaned vectors. This wastes Pinecone storage quota and can pollute search results with stale data.

**Combined fix required:**
1. Include a content hash in the node ID (so the docstore detects updates).
2. Before ingesting a file's updated nodes, delete any existing Pinecone vectors whose metadata `source` matches that file path (per-file upsert logic).
3. Evict the old node IDs from the docstore so the pipeline doesn't keep them as "known".

---

## Before vs After

### `src/chunker.py` — `Chunk.to_llama_node()` (lines 28–32)

**Before:**
```python
def to_llama_node(self) -> TextNode:
    node_id = hashlib.md5(
        (self.metadata["source"] + str(self.metadata["chunk_index"])).encode()
    ).hexdigest()
    return TextNode(id_=node_id, text=self.text, metadata=self.metadata)
```

**After:**
```python
def to_llama_node(self) -> TextNode:
    content_hash = hashlib.md5(self.text.encode()).hexdigest()[:8]
    node_id = hashlib.md5(
        (self.metadata["source"] + str(self.metadata["chunk_index"]) + content_hash).encode()
    ).hexdigest()
    return TextNode(id_=node_id, text=self.text, metadata=self.metadata)
```

### `ingest.py` — per-file upsert cleanup (after line 72, inside the `for doc in file_docs:` loop)

**Before (the loop body, simplified):**
```python
for doc in file_docs:
    nodes = node_parser._parse_nodes([doc])
    if not nodes:
        skipped += 1
        continue
    for node in nodes:
        node.set_content(anonymize_text(node.get_content()))
    all_nodes.extend(nodes)
    files_processed += 1
```

**After (add upsert cleanup per file before the nodes are appended):**
```python
for doc in file_docs:
    nodes = node_parser._parse_nodes([doc])
    if not nodes:
        skipped += 1
        continue
    for node in nodes:
        node.set_content(anonymize_text(node.get_content()))

    # --- UPSERT: delete old Pinecone vectors and evict stale docstore entries ---
    source_path = doc.metadata["file_path"]
    _delete_stale_vectors(pc_index, docstore, source_path)
    # -------------------------------------------------------------------------

    all_nodes.extend(nodes)
    files_processed += 1
```

**New helper function to add near the top of `ingest.py`:**
```python
def _delete_stale_vectors(pc_index, docstore: SimpleDocumentStore, source_path: str) -> None:
    """Delete all Pinecone vectors and docstore entries for a given source file."""
    try:
        results = pc_index.query(
            vector=[0.0] * 1024,
            top_k=1000,
            filter={"source": {"$eq": source_path}},
            include_metadata=False,
        )
        ids_to_delete = [m["id"] for m in results.get("matches", [])]
        if ids_to_delete:
            pc_index.delete(ids=ids_to_delete)
            logger.info("Deleted %d stale vectors for %s", len(ids_to_delete), source_path)
            # Evict from docstore so the pipeline does not treat them as "known"
            for node_id in ids_to_delete:
                try:
                    docstore.delete_document(node_id)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Could not clean up stale vectors for %s: %s", source_path, e)
```

---

## What Gets Deleted

- No files are deleted.
- No existing functionality is removed.
- The only "deletion" is the runtime removal of stale Pinecone records for a source file before re-ingesting it.

---

## Step-by-Step Implementation

1. **Edit `src/chunker.py`, lines 28–32**: Replace the `to_llama_node` method body with the content-hash-augmented version.

2. **Edit `ingest.py`**: Add the `_delete_stale_vectors` helper function near the top (after the imports, before `CACHE_PATH`).

3. **Edit `ingest.py`**: Inside the `for doc in file_docs:` loop (after the anonymize loop, before `all_nodes.extend(nodes)`), insert the call to `_delete_stale_vectors(pc_index, docstore, source_path)`.

4. **Verify imports**: `SimpleDocumentStore` is already imported in `ingest.py`. No new imports needed.

---

## Files to Modify

- `src/chunker.py` — add content hash to node ID
- `ingest.py` — add `_delete_stale_vectors` helper and call it per file

---

## Test Plan

**Test command:**
```
cd "d:\Users\Agents course\Lesson2 - RAG + Embedding" && python -m pytest tests/ -v
```
Or if pytest is not installed:
```
cd "d:\Users\Agents course\Lesson2 - RAG + Embedding" && python tests/test_chunker.py && python tests/test_embeddings.py
```

**What the existing tests verify (must still pass):**
- `test_chunker.py`: Builds a temp DOCX, chunks it, checks section titles, metadata fields (template_type, document_date, birth_date, client_name), chunk count > 0.
- `test_embeddings.py`: Verifies batching behavior and save/load of the vector store.

**New assertion to add to `test_chunker.py`** (verifies the fix):
```python
# --- Verify BUG-001 fix: node ID must include content hash ---
from src.chunker import Chunk

chunk_a = Chunk(text="Hello world", metadata={"source": "/path/file.docx", "chunk_index": 0})
chunk_b = Chunk(text="Updated text", metadata={"source": "/path/file.docx", "chunk_index": 0})
chunk_c = Chunk(text="Hello world", metadata={"source": "/path/file.docx", "chunk_index": 0})

node_a = chunk_a.to_llama_node()
node_b = chunk_b.to_llama_node()
node_c = chunk_c.to_llama_node()

assert node_a.id_ != node_b.id_, "Same path+index but different text must produce different node IDs"
assert node_a.id_ == node_c.id_, "Identical chunks must produce identical node IDs (deterministic)"
print("BUG-001 node-ID assertions passed")
```

**Expected test output:**
```
ALL ASSERTIONS PASSED
BUG-001 node-ID assertions passed
```

---

## Trade-offs

| Trade-off | Impact |
|---|---|
| Pinecone query before each file ingest | One extra Pinecone `query` call per `.docx` file. Cost: negligible for typical document counts (< 100 files). |
| Zero-vector query | We use `[0.0] * 1024` as a dummy query vector; this is a workaround since Pinecone's `fetch` API requires knowing the IDs upfront. Pinecone's metadata filter on `query` is the correct API for this. If the embedding dimension changes, the vector length must be updated. |
| Idempotency | Running ingest twice on the same unchanged file now deletes and re-inserts vectors (because `_delete_stale_vectors` always deletes). This is a mild regression vs. the original design (which was fully idempotent for unchanged files). To restore idempotency for unchanged files, the content-hash node ID already handles it: after the first delete+reinsert, subsequent runs will see the same node IDs already in the docstore and skip them. |
| top_k=1000 limit | If a single file produces more than 1000 chunks, the Pinecone query won't return all stale IDs. In practice, Hebrew therapy documents are short (<30 chunks). |

---

## Rollback Plan

If the fix causes regressions:

1. **Revert `src/chunker.py`**: remove the `content_hash` line and restore the original two-line `node_id` computation.
2. **Revert `ingest.py`**: remove the `_delete_stale_vectors` function and its call in the loop.
3. **Optional cleanup**: run `python ingest.py` with the reverted code to restore the docstore to its pre-fix state. Or delete `storage/pipeline_cache/docstore.json` and re-ingest from scratch.

No data is permanently lost by either applying or rolling back this fix.
