"""Tests for filename classification (PLAN.md items 1–5, T1 decisions 1-א/ב/ג)."""

import asyncio
from types import SimpleNamespace

import pytest

from app.ingestion import pipeline
from app.ingestion.classify import classify_filename


@pytest.mark.parametrize("filename, expected", [
    ("סיכום אבחון ישראל ישראלי.docx", ["diagnosis"]),
    ("אבחון ישראל ישראלי.docx", ["diagnosis"]),                                   # item 2
    ("ישראל ישראלי סיכום טיפול.docx", ["clinic_visit_summary"]),                  # item 1
    ("בקשה להמשך טיפול ישראל ישראלי.docx", ["clinic_visit_summary"]),             # item 4
    ("סיכום ביניים ובקשה להמשך ישראל ישראלי.docx", ["clinic_visit_summary"]),     # item 4
    ("סיכום טיפול ובקשה להמשך ישראל ישראלי.docx", ["clinic_visit_summary"]),      # same type twice
    ("ישראל ישראלי תוכנית טיפול הדרכתי.docx", ["treatment_plan"]),                # item 1
    ("תכנית טיפול ישראל ישראלי.docx", ["treatment_plan"]),                        # 1-ג
    ("סיכום  אבחון ישראל ישראלי.docx", ["diagnosis"]),                            # double space
    ("חוזה טיפול ישראל ישראלי.docx", []),                                         # 1-א
    ("דף עבודה.docx", []),                                                        # 1-א
    ("סיכום אבחון ותוכנית טיפול ישראל.docx", ["diagnosis", "treatment_plan"]),    # 1-ב
])
def test_classify_filename(filename, expected):
    assert classify_filename(filename) == expected


def _ingest_one(tmp_path, monkeypatch, filename):
    patient = tmp_path / "ישראל ישראלי"
    patient.mkdir()
    (patient / filename).write_bytes(b"not a docx")   # opening it would be an error
    monkeypatch.setattr(pipeline, "load_ner_model", lambda models_dir: None)
    config = SimpleNamespace(models_dir="", run_mode="test", data_dir=str(tmp_path))
    [(_, result)] = asyncio.run(pipeline.run_ingestion(
        [str(tmp_path)], config, {}, None, None, dry_run=True))
    return result


def test_unrecognised_file_is_ignored_with_explanation(tmp_path, monkeypatch):
    result = _ingest_one(tmp_path, monkeypatch, "דף עבודה ישראל.docx")
    assert result.status == "ignored"
    assert "לא זוהה כמסמך קליני" in result.message


def test_filename_with_two_types_is_blocked(tmp_path, monkeypatch):
    result = _ingest_one(tmp_path, monkeypatch, "סיכום אבחון ותוכנית טיפול ישראל.docx")
    assert result.status == "blocked"
    assert "(אבחון, תוכנית טיפול)" in result.message
