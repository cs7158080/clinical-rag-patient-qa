"""Tests for delete_rows_for_file cascade into domain_findings (PLAN.md O4.1-1)."""

from app.storage.db import (
    delete_rows_for_file,
    fetch_document_sections,
    fetch_domain_findings_for_date,
    insert_domain_finding,
    insert_family_a_chunk,
)
from app.storage.models import DomainFinding, FamilyAChunk

_PID = "p1"
_DATE = "2026-07-27"
_DIAGNOSIS = "/c/אברהם כהן/סיכום אבחון אברהם כהן.docx"
_PLAN = "/c/אברהם כהן/תוכנית טיפול הדרכתי אברהם כהן.docx"


def _seed(db_path):
    insert_family_a_chunk(db_path, FamilyAChunk(
        patient_id=_PID, template_type="diagnosis", session_date=_DATE,
        section="רקע", text_deidentified="רקע", source_file_path=_DIAGNOSIS,
    ))
    insert_domain_finding(db_path, DomainFinding(
        patient_id=_PID, session_date=_DATE,
        domain_name="הבנת שפה", domain_text_deidentified="ממצא",
    ))
    # As T2 will store plan header fields: same patient, same date as the diagnosis.
    insert_family_a_chunk(db_path, FamilyAChunk(
        patient_id=_PID, template_type="treatment_plan", session_date=_DATE,
        section="מטרת_על", text_deidentified="מטרה", source_file_path=_PLAN,
    ))


def test_deleting_plan_keeps_diagnosis_domains(db_path):      # fails before the fix
    _seed(db_path)
    delete_rows_for_file(db_path, _PLAN)
    assert len(fetch_domain_findings_for_date(db_path, _PID, _DATE)) == 1
    assert fetch_document_sections(db_path, _PID, "treatment_plan") == []
    assert len(fetch_document_sections(db_path, _PID, "diagnosis")) == 1


def test_deleting_diagnosis_still_deletes_its_domains(db_path):
    _seed(db_path)
    delete_rows_for_file(db_path, _DIAGNOSIS)
    assert fetch_domain_findings_for_date(db_path, _PID, _DATE) == []
    assert fetch_document_sections(db_path, _PID, "diagnosis") == []
    assert len(fetch_document_sections(db_path, _PID, "treatment_plan")) == 1
