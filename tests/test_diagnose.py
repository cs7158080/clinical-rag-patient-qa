"""Tests for scripts/diagnose_ingestion.compare (PLAN.md section 7, T1 6-א)."""

from app.ingestion.result import IngestResult
from scripts.diagnose_ingestion import compare


def test_compare_checks_document_date_only_when_expected():
    result = IngestResult("ok", document_date="2026-03-01")
    assert compare(result, {"status": "ok"}) == []
    assert compare(result, {"status": "ok", "document_date": "2026-03-01"}) == []
    assert compare(result, {"status": "ok", "document_date": ""}) == [
        "document_date '2026-03-01' != expected ''"]
