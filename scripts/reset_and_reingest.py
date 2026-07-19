"""
reset_and_reingest.py — Wipe all stored data and re-ingest from scratch.

Run from the project root:
    python scripts/reset_and_reingest.py
"""

import asyncio
import os
import sys

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_config
from app.logging_setup import setup_logging
from app.storage.db import init_db
from app.storage.pinecone_client import init_pinecone
from app.deidentification.reid_map import load as load_reid_map
from app.ingestion.pipeline import run_ingestion


def _label_for(path: str, roots: list) -> str:
    """Relpath of *path* against the root that contains it; the raw path as fallback."""
    for root in roots:
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            continue
        if not rel.startswith(".."):
            return rel
    return path


def reset(config, db_path: str, reid_map_path: str) -> None:
    print("=== RESET ===")

    # 1. Delete SQLite DB
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"Deleted DB: {db_path}")
    else:
        print(f"DB not found (skipping): {db_path}")

    # 2. Delete reid_map
    if os.path.exists(reid_map_path):
        os.remove(reid_map_path)
        print(f"Deleted reid_map: {reid_map_path}")
    else:
        print(f"reid_map not found (skipping): {reid_map_path}")

    # 3. Clear Pinecone index
    print("Connecting to Pinecone...")
    try:
        index = init_pinecone(config.pinecone_api_key, config.pinecone.index_name)
        index.delete(delete_all=True)
        print(f"Pinecone index '{config.pinecone.index_name}' cleared.")
    except Exception as e:
        print(f"Pinecone clear failed: {e}")
        print("You may need to clear it manually from the Pinecone console.")


async def reingest(config, db_path: str, reid_map_path: str) -> None:
    print("\n=== RE-INGEST ===")

    init_db(db_path)
    print(f"Fresh DB created at: {db_path}")

    try:
        pinecone_index = init_pinecone(config.pinecone_api_key, config.pinecone.index_name)
    except Exception as e:
        print(f"Pinecone unavailable: {e}")
        pinecone_index = None

    reid_map = load_reid_map(reid_map_path)

    results = await run_ingestion(
        patients_roots=config.patients_roots,
        config=config,
        reid_map=reid_map,
        db_path=db_path,
        pinecone_index=pinecone_index,
    )

    print("\n=== RESULTS ===")
    ok = skipped = blocked = error = 0
    for path, result in results:
        label = _label_for(path, config.patients_roots)
        print(f"  {result:30s}  {label}")
        if result == "ok":
            ok += 1
        elif result == "skipped":
            skipped += 1
        elif result.startswith("blocked"):
            blocked += 1
        else:
            error += 1

    print(f"\nSummary: {ok} ok | {skipped} skipped | {blocked} blocked | {error} errors")


def main():
    config = get_config()
    setup_logging(config)

    data_dir = config.data_dir
    os.makedirs(data_dir, exist_ok=True)

    db_path = os.path.join(data_dir, "clinical_rag.db")
    reid_map_path = os.path.join(data_dir, "reid_map.json")

    reset(config, db_path, reid_map_path)
    asyncio.run(reingest(config, db_path, reid_map_path))


if __name__ == "__main__":
    main()
