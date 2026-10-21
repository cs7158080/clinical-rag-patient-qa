"""Tests for date-of-birth normalisation and storage (PLAN.md item 28a, T2 28a-1 … 28a-4)."""

import pytest

from app.ingestion import pipeline
from app.ingestion.adapter_b import parse_treatment_plan
from app.storage.db import get_patient_metadata, upsert_patient_metadata
from tests.fixtures.docx_factory import write_docx


@pytest.mark.parametrize("raw, expected", [
    ("08/08/2008", "2008-08-08"),
    ("1/1/24", "2024-01-01"),
    (None, None),
    ("", None),
])
def test_full_date_of_birth_is_stored_as_iso(raw, expected):              # 28a-2
    assert pipeline.normalize_date_of_birth(raw) == (expected, [])


@pytest.mark.parametrize("raw", ["ספטמבר 2008", "08/08", "01/01/2099", "31/13/2008"])
def test_invalid_date_of_birth_is_a_warning_and_not_stored(raw):         # 28a-2
    assert pipeline.normalize_date_of_birth(raw) == (
        None, [f"תאריך הלידה '{raw}' אינו תאריך לידה תקין — לא נשמר, כדאי לבדוק"])


def test_plan_reads_date_of_birth_label_variants(tmp_path):              # 28a-1, T1 3-ה
    path = write_docx(tmp_path / "תוכנית טיפול ישראל ישראלי.docx", [
        "שם: ישראל ישראלי", "ת.ל: 08/08/2008", "01/03/2026", "מפגש."])
    parsed = parse_treatment_plan(str(path), "ישראל ישראלי")
    assert parsed["header"]["date_of_birth"] == "08/08/2008"


def _workflow(db_path):
    return pipeline.IngestionWorkflow(config=None, reid_map={}, db_path=db_path, pinecone_index=None)


def test_conflicting_date_of_birth_keeps_the_first_value(db_path):       # 28a-3, 28a-4
    wf = _workflow(db_path)
    assert wf._store_date_of_birth("p1", "2022-06-20", "/a.docx") == []
    assert wf._store_date_of_birth("p1", "2022-02-20", "/b.docx") == [
        "תאריך הלידה בקובץ (20/02/2022) שונה מזה שבמסמך אחר של המטופל (20/06/2022) — "
        "נשמר 20/06/2022, כדאי לבדוק"]
    assert get_patient_metadata(db_path, "p1")["date_of_birth"] == "2022-06-20"

    # A fix in the file the stored value came from replaces it.
    assert wf._store_date_of_birth("p1", "2022-02-20", "/a.docx") == []
    assert get_patient_metadata(db_path, "p1")["date_of_birth"] == "2022-02-20"


def test_legacy_raw_date_of_birth_is_replaced_silently(db_path):          # stored before T2
    upsert_patient_metadata(db_path, "p1", "date_of_birth", "02/02/2022")
    assert _workflow(db_path)._store_date_of_birth("p1", "2022-02-02", "/a.docx") == []
    assert get_patient_metadata(db_path, "p1")["date_of_birth"] == "2022-02-02"
