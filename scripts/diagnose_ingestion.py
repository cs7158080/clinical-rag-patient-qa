"""
diagnose_ingestion.py — dry-run every patient file and compare with the expected outcomes.

Runs parsing, de-identification and the Pass 2 gate exactly as a real ingest, then
stops: nothing is sent to Cohere / Pinecone / Anthropic, nothing is written to the
DB, and the re-id map lives in memory only (starts empty, never saved).

Run from the project root:
    uv run python scripts/diagnose_ingestion.py [expected.json]
Default expected file: .claude/plans/real-data/expected-outcomes.json (local, not in git).
"""

import asyncio
import json
import os
import sys

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_config
from app.logging_setup import setup_logging
from app.ingestion.pipeline import run_ingestion

DEFAULT_EXPECTED = os.path.join(".claude", "plans", "real-data", "expected-outcomes.json")


def _label_for(path: str, roots: list) -> str:
    """'patient folder/file.docx' relative to the root that contains *path*."""
    for root in roots:
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            continue
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    return path


def compare(result, expected) -> list[str]:
    """Differences between a dry-run result and its expected entry (empty list = match)."""
    if expected is None:
        return ["no expected entry for this file"]
    diffs = []
    if result.status != expected["status"]:
        diffs.append(f"status {result.status} != expected {expected['status']}")
    block = expected.get("block")
    if block and block not in result.message:
        diffs.append(f"block message does not contain: {block}")
    want = expected.get("warnings", [])
    missing = [w for w in want if not any(w in got for got in result.warnings)]
    if missing:
        diffs.append(f"missing warnings: {missing}")
    if len(result.warnings) != len(want):
        diffs.append(f"warning count {len(result.warnings)} != expected {len(want)}")
    for key, n in expected.get("counts", {}).items():
        if result.counts.get(key) != n:
            diffs.append(f"count {key}={result.counts.get(key)} != expected {n}")
    return diffs


async def main(expected_path: str) -> None:
    config = get_config()
    setup_logging(config)
    with open(expected_path, encoding="utf-8") as fh:
        expected = json.load(fh)["files"]

    results = await run_ingestion(
        patients_roots=config.patients_roots,
        config=config,
        reid_map={},
        db_path=None,
        pinecone_index=None,
        dry_run=True,
    )

    matched = 0
    seen = set()
    for path, result in sorted(results, key=lambda pr: pr[0]):
        label = _label_for(path, config.patients_roots)
        seen.add(label)
        diffs = compare(result, expected.get(label))
        matched += not diffs
        print(f"{'MATCH' if not diffs else 'DIFF '}  {result.status:8s} {label}")
        if result.message:
            print(f"         message: {result.message}")
        for w in result.warnings:
            print(f"         warning: {w}")
        if result.counts:
            print("         counts:  " + ", ".join(f"{k}={v}" for k, v in result.counts.items()))
        for d in diffs:
            print(f"         != {d}")

    for label in sorted(set(expected) - seen):
        print(f"MISSING  {label} (expected but not found on disk)")

    print(f"\n{matched}/{len(expected)} match target")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXPECTED))
