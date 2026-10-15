"""Tests for IngestResult rendering and ingestion error reporting (PLAN.md 24b, 24c)."""

import asyncio
import logging
from types import SimpleNamespace

from app.deidentification.validation import validate_deidentified
from app.ingestion import pipeline
from app.ingestion.result import IngestResult, format_report

_WARNING = "התאריך '20/06' נכתב בלי שנה — הונחה השנה 2026"


def test_report_all_statuses():
    report = format_report([
        ("/p/a/ok.docx", IngestResult("ok", warnings=[_WARNING])),
        ("/p/a/same.docx", IngestResult("skipped")),
        ("/p/a/חוזה.docx", IngestResult("ignored", "לא זוהה כמסמך קליני ולא נטען")),
        ("/p/a/blk.docx", IngestResult("blocked", "לא נמצא תאריך מסמך")),
        ("/p/a/err.docx", IngestResult("error", "שגיאה לא צפויה")),
    ])
    lines = report.splitlines()
    assert lines[0] == ("סיום טעינה — 1 נטענו, 1 ללא שינוי, 1 חסומים, 1 שגיאות, "
                        "1 דולגו (לא זוהו כמסמך קליני), 1 אזהרות")
    assert lines.index(f"      ⚠  {_WARNING}") == lines.index("✓  ok.docx") + 1
    assert "○  same.docx (ללא שינוי)" in lines
    assert "○  חוזה.docx — דולג: לא זוהה כמסמך קליני ולא נטען" in lines
    assert "✗  blk.docx — חסום: לא נמצא תאריך מסמך" in lines
    assert "✗  err.docx — שגיאה: שגיאה לא צפויה" in lines


def test_every_validation_failure_has_hebrew_message():
    for failure_type in ("regex", "reid_map", "ner_rescan"):
        assert pipeline._VALIDATION_MESSAGES[failure_type]


def test_pass2_logs_the_found_value(caplog):
    with caplog.at_level(logging.WARNING, logger="app.deidentification.validation"):
        assert not validate_deidentified("טלפון 050-1234567", {}).passed
        assert not validate_deidentified("שלום אירית", {"PERSON_x": "אירית"}).passed
    assert "050-1234567" in caplog.text
    assert "אירית" in caplog.text


def test_unhandled_exception_becomes_hebrew_error_with_traceback(tmp_path, monkeypatch, caplog):
    patient = tmp_path / "מטופל"
    patient.mkdir()
    (patient / "תוכנית טיפול מטופל.docx").write_bytes(b"x")
    (patient / "~$lock.docx").write_bytes(b"x")               # Word lock file — skipped

    async def boom_step(**kwargs):
        raise RuntimeError("cohere down")

    monkeypatch.setattr(pipeline, "load_ner_model", lambda models_dir: None)
    monkeypatch.setattr(pipeline.IngestionWorkflow, "run", lambda self, **kw: boom_step(**kw))
    config = SimpleNamespace(models_dir="", run_mode="test", data_dir=str(tmp_path))

    with caplog.at_level(logging.ERROR, logger="app.ingestion.pipeline"):
        results = asyncio.run(pipeline.run_ingestion([str(tmp_path)], config, {}, "unused.db", None))

    assert len(results) == 1
    path, result = results[0]
    assert result.status == "error"
    assert "שגיאה לא צפויה" in result.message and "boom_step" in result.message
    record = next(r for r in caplog.records if "unhandled error" in r.getMessage())
    assert record.exc_info is not None                       # full traceback logged
    assert path in record.getMessage()


def test_dry_run_stops_before_storage(tmp_path, monkeypatch):
    from docx import Document
    from app.deidentification import validation

    patient = tmp_path / "ישראל ישראלי"
    patient.mkdir()
    doc = Document()
    for line in ['בס"ד', "ישראל ישראלי", "01/03/2026", "שיחה עם ההורים."]:
        doc.add_paragraph(line)
    doc.save(patient / "תוכנית טיפול ישראל ישראלי.docx")

    def forbidden(*args, **kwargs):
        raise AssertionError("dry run must not reach the DB or external services")

    monkeypatch.setattr(pipeline, "load_ner_model", lambda models_dir: None)
    monkeypatch.setattr(validation, "_check_ner_rescan", lambda text: True)
    for name in ("get_file_hash", "get_cohere_embedding", "upsert_vectors",
                 "mark_file_ingested", "save_reid_map"):
        monkeypatch.setattr(pipeline, name, forbidden)
    config = SimpleNamespace(models_dir="", run_mode="test", data_dir=str(tmp_path))
    reid_map = {}

    results = asyncio.run(pipeline.run_ingestion(
        [str(tmp_path)], config, reid_map, None, None, dry_run=True))

    [(path, result)] = results
    assert result.status == "ok", result.message
    assert result.counts == {"sections": 0, "domains": 0, "goals": 0, "sessions": 1}
    assert reid_map                      # patient token added — in memory only
