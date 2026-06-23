# ingest.py
import glob
import logging
import os
import sys

from dotenv import load_dotenv
from llama_index.core import Document, Settings
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.vector_stores.pinecone import PineconeVectorStore
from pinecone import Pinecone

from src.chunker import DocxTemplateNodeParser, TemplateRegistry
from src.embeddings import CohereEmbedder
from src.pii_filter import anonymize_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from netfree_unstrict_ssl import unstrict_ssl
unstrict_ssl()

CACHE_PATH = "storage/pipeline_cache"


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
            for node_id in ids_to_delete:
                try:
                    docstore.delete_document(node_id)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Could not clean up stale vectors for %s: %s", source_path, e)


def ingest_folder(folder_path: str) -> dict:
    load_dotenv()
    cohere_key = os.environ.get("COHERE_API_KEY")
    pinecone_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_INDEX_NAME")

    if not cohere_key:
        raise RuntimeError("COHERE_API_KEY not set in environment")
    if not pinecone_key:
        raise RuntimeError("PINECONE_API_KEY not set in environment")
    if not index_name:
        raise RuntimeError("PINECONE_INDEX_NAME not set in environment")

    registry = TemplateRegistry("templates_config.yaml")
    node_parser = DocxTemplateNodeParser(registry)
    embedder = CohereEmbedder(cohere_key)

    Settings.embed_model = embedder
    Settings.llm = None

    pc = Pinecone(api_key=pinecone_key)
    pc_index = pc.Index(index_name)
    vector_store = PineconeVectorStore(pinecone_index=pc_index)

    docx_files = glob.glob(os.path.join(folder_path, "**", "*.docx"), recursive=True)
    if not docx_files:
        logger.warning("No .docx files found in %s", folder_path)
        return {"files_processed": 0, "chunks": 0, "skipped": 0, "added": 0, "unchanged": 0}

    # Create stub Documents so DocxTemplateNodeParser can read file_path from metadata
    file_docs = [
        Document(text="", metadata={"file_path": os.path.abspath(p)})
        for p in docx_files
    ]

    os.makedirs(CACHE_PATH, exist_ok=True)
    docstore_path = os.path.join(CACHE_PATH, "docstore.json")
    docstore = (
        SimpleDocumentStore.from_persist_dir(CACHE_PATH)
        if os.path.exists(docstore_path)
        else SimpleDocumentStore()
    )

    all_nodes = []
    files_processed = 0
    skipped = 0

    for doc in file_docs:
        nodes = node_parser._parse_nodes([doc])
        if not nodes:
            logger.warning("Skipping %s — 0 nodes produced", doc.metadata["file_path"])
            skipped += 1
            continue
        for node in nodes:
            node.set_content(anonymize_text(node.get_content()))
        _delete_stale_vectors(pc_index, docstore, doc.metadata["file_path"])
        all_nodes.extend(nodes)
        files_processed += 1
        logger.info("Processed %s — %d nodes", doc.metadata["file_path"], len(nodes))

    if not all_nodes:
        logger.warning("No nodes produced from any file in %s", folder_path)
        return {"files_processed": 0, "chunks": 0, "skipped": skipped, "added": 0, "unchanged": 0}

    logger.info("Ingesting %d nodes into Pinecone...", len(all_nodes))

    pipeline = IngestionPipeline(
        transformations=[embedder],
        vector_store=vector_store,
        docstore=docstore,
    )

    new_nodes = pipeline.run(nodes=all_nodes, show_progress=True)
    pipeline.persist(CACHE_PATH)
    logger.info("Saved pipeline cache to %s", CACHE_PATH)

    return {
        "files_processed": files_processed,
        "chunks": len(all_nodes),
        "skipped": skipped,
        "added": len(new_nodes),
        "unchanged": len(all_nodes) - len(new_nodes),
    }


def main():
    stats = ingest_folder("data/clients")
    if stats["files_processed"] == 0:
        logger.error("No files processed. Aborting.")
        sys.exit(1)
    print("\nIngestion complete:")
    print(f"  Files processed: {stats['files_processed']}")
    print(f"  Chunks produced: {stats['chunks']}")
    print(f"  Files skipped:   {stats['skipped']}")
    print(f"  Added:           {stats['added']}")
    print(f"  Unchanged:       {stats['unchanged']}")


if __name__ == "__main__":
    main()
