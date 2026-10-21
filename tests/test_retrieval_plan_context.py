"""Plan header fields + goals records prepended to treatment-plan answers (PLAN.md O4.2, T2 5-ו, 5-ז)."""

from app.prompts.qa import NO_RESULTS_MESSAGE
from app.query import retrieval
from app.storage.db import insert_family_a_chunk, insert_treatment_goals, insert_treatment_session
from app.storage.models import (
    FamilyAChunk,
    QueryParams,
    TreatmentGoalsChunk,
    TreatmentSessionChunk,
)

_PID = "p1"
_HEADER = "[מטרת_על מתאריך 2026-03-01]\nשיפור השיח"


def _seed(db_path, with_session=True):
    insert_family_a_chunk(db_path, FamilyAChunk(
        patient_id=_PID, template_type="treatment_plan", session_date="2026-03-01",
        section="מטרת_על", text_deidentified="שיפור השיח", source_file_path="/plan.docx",
    ))
    if with_session:
        for d in ("2026-03-05", "2026-03-20"):
            insert_treatment_session(db_path, TreatmentSessionChunk(
                patient_id=_PID, session_date=d, session_text_deidentified=f"מפגש {d}",
                source_file_path="/plan.docx",
            ))


def test_sessions_answer_starts_with_plan_header(db_path):              # 5-ו
    _seed(db_path)
    result = retrieval._fetch_treatment_sessions(db_path, QueryParams(_PID, "summarize"))
    assert result.chunks[0] == _HEADER
    assert len(result.chunks) == 3


def test_sqlite_fallback_starts_with_plan_header(db_path):              # 5-ו (Pinecone down)
    _seed(db_path)
    result = retrieval._pinecone_retrieve(QueryParams(_PID, "find_specific", topic="שיח"),
                                          None, db_path, None)
    assert result.chunks[0] == _HEADER


def test_pinecone_answer_starts_with_plan_header(db_path, monkeypatch):   # 5-ו
    _seed(db_path)
    monkeypatch.setattr(retrieval.pinecone_client, "get_cohere_query_embedding",
                        lambda text, key, model: [0.0])
    monkeypatch.setattr(retrieval.pinecone_client, "query_pinecone",
                        lambda *args, **kwargs: [{"metadata": {
                            "patient_id": _PID, "session_date": "2026-03-05",
                            "chunk_type": "session_summary"}}])
    config = type("C", (), {"cohere_api_key": "", "cohere": type("M", (), {"model": ""})})
    result = retrieval._pinecone_retrieve(QueryParams(_PID, "find_specific", topic="שיח"),
                                          config, db_path, object())
    assert result.chunks == [_HEADER, "[טיפול מתאריך 2026-03-05]\nמפגש 2026-03-05"]


def test_compare_puts_plan_header_first_in_before(db_path):            # 5-ו (2)
    _seed(db_path)
    result = retrieval._fetch_compare(
        db_path, QueryParams(_PID, "compare_progress", date_from="2026-03-10"))
    assert result.chunks["before"][0] == _HEADER
    assert _HEADER not in result.chunks["after"]


def test_goals_records_follow_the_plan_header(db_path):                 # 5-ז
    _seed(db_path)
    for d, text in (("2026-03-10", "תחביר: משפט מורכב"), ("2026-03-02", "לקסיקון: שליפה")):
        insert_treatment_goals(db_path, TreatmentGoalsChunk(
            patient_id=_PID, session_date=d, goals_text_deidentified=text,
            source_file_path="/plan.docx",
        ))
    result = retrieval._fetch_treatment_sessions(
        db_path, QueryParams(_PID, "compare_progress"))
    assert result.chunks[:3] == [
        _HEADER,
        "[יעדי טיפול מתאריך 2026-03-02]\nלקסיקון: שליפה",
        "[יעדי טיפול מתאריך 2026-03-10]\nתחביר: משפט מורכב",
    ]


def test_no_sessions_still_means_no_results(db_path):                  # 5-ו (1)
    _seed(db_path, with_session=False)
    assert retrieval._fetch_treatment_sessions(
        db_path, QueryParams(_PID, "summarize")) == NO_RESULTS_MESSAGE
