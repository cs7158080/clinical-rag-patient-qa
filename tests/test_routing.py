"""
test_routing.py — Query routing and SQLite CRUD unit tests.

No external API calls are made. All tests run against a local SQLite fixture
provided by the db_path conftest fixture.

Modules under test:
    app.query.router       — deterministic routing logic
    app.storage.db         — SQLite CRUD helpers
    app.storage.models     — shared dataclasses
"""

import pytest

from app.query.router import route, FIXED_DOMAINS
from app.storage.models import (
    FamilyAChunk,
    QueryParams,
    TreatmentSessionChunk,
)
from app.storage.db import (
    insert_family_a_chunk,
    fetch_family_a_sections,
    insert_treatment_session,
    fetch_treatment_sessions,
    upsert_patient_metadata,
    get_patient_metadata,
)


# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------

_PATIENT_ID = "test_patient_abc123"


# ---------------------------------------------------------------------------
# Routing — summarize intent
# ---------------------------------------------------------------------------

def test_route_summarize_treatment():
    """summarize + treatment_plan → treatment_sessions_sqlite."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="summarize",
        template_type="treatment_plan",
    )
    decision = route(params)

    assert decision.strategy == "treatment_sessions_sqlite"


def test_route_summarize_diagnosis():
    """summarize + diagnosis → family_a_sqlite."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="summarize",
        template_type="diagnosis",
    )
    decision = route(params)

    assert decision.strategy == "family_a_sqlite"


def test_route_summarize_clinic_visit_summary():
    """summarize + clinic_visit_summary → family_a_sqlite."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="summarize",
        template_type="clinic_visit_summary",
    )
    decision = route(params)

    assert decision.strategy == "family_a_sqlite"


def test_route_summarize_null_template():
    """summarize + None template → treatment_sessions_sqlite (default: treatment sessions)."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="summarize",
        template_type=None,
    )
    decision = route(params)

    assert decision.strategy == "treatment_sessions_sqlite"


# ---------------------------------------------------------------------------
# Routing — check_domain intent
# ---------------------------------------------------------------------------

def test_route_check_domain_fixed():
    """check_domain + fixed taxonomy topic → domain_sqlite."""
    # Use a known fixed domain: זיכרון שמיעתי
    assert "זיכרון שמיעתי" in FIXED_DOMAINS  # guard: verify test assumption

    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="check_domain",
        topic="זיכרון שמיעתי",
    )
    decision = route(params)

    assert decision.strategy == "domain_sqlite"


def test_route_check_domain_open():
    """check_domain + topic not in fixed taxonomy → pinecone."""
    open_topic = "דיבור"  # not in the 13 fixed domains
    assert open_topic not in FIXED_DOMAINS  # guard: verify test assumption

    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="check_domain",
        topic=open_topic,
    )
    decision = route(params)

    assert decision.strategy == "pinecone"


def test_route_check_domain_all_fixed_domains():
    """Every domain in the fixed taxonomy must route to domain_sqlite."""
    for domain in FIXED_DOMAINS:
        params = QueryParams(
            patient_id=_PATIENT_ID,
            intent="check_domain",
            topic=domain,
        )
        decision = route(params)
        assert decision.strategy == "domain_sqlite", (
            f"Expected domain_sqlite for fixed domain '{domain}', got '{decision.strategy}'"
        )


# ---------------------------------------------------------------------------
# Routing — find_specific intent
# ---------------------------------------------------------------------------

def test_route_find_specific_no_topic():
    """find_specific without a topic → treatment_sessions_sqlite (nothing to embed)."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="find_specific",
    )
    decision = route(params)

    assert decision.strategy == "treatment_sessions_sqlite"


def test_route_find_specific_with_topic():
    """find_specific + free-text topic → pinecone (semantic search)."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="find_specific",
        topic="צליל ר",
    )
    decision = route(params)

    assert decision.strategy == "pinecone"


def test_route_find_specific_diagnosis():
    """find_specific + diagnosis → family_a_sqlite (Family A is retrieved directly)."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="find_specific",
        template_type="diagnosis",
    )
    decision = route(params)

    assert decision.strategy == "family_a_sqlite"


# ---------------------------------------------------------------------------
# Routing — compare_progress intent
# ---------------------------------------------------------------------------

def test_route_compare_progress():
    """compare_progress → compare_sqlite regardless of template type."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="compare_progress",
        date_from="2025-01-01",
    )
    decision = route(params)

    assert decision.strategy == "compare_sqlite"


def test_route_compare_progress_no_date():
    """compare_progress without date_from → treatment_sessions_sqlite (progress over time)."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="compare_progress",
    )
    decision = route(params)

    assert decision.strategy == "treatment_sessions_sqlite"


# ---------------------------------------------------------------------------
# SQLite — FamilyAChunk insert and fetch
# ---------------------------------------------------------------------------

def test_db_insert_and_fetch(db_path):
    """Insert a FamilyAChunk and fetch it back; all fields must match."""
    chunk = FamilyAChunk(
        patient_id=_PATIENT_ID,
        template_type="diagnosis",
        session_date="2025-01-15",
        section="רקע",
        text_deidentified="טקסט לדוגמה בלי מידע רגיש.",
        source_file_path="/fake/path/diagnosis_test.docx",
    )

    insert_family_a_chunk(db_path, chunk)
    rows = fetch_family_a_sections(
        db_path,
        patient_id=_PATIENT_ID,
        template_type="diagnosis",
    )

    assert len(rows) == 1
    fetched = rows[0]
    assert fetched.patient_id == chunk.patient_id
    assert fetched.template_type == chunk.template_type
    assert fetched.session_date == chunk.session_date
    assert fetched.section == chunk.section
    assert fetched.text_deidentified == chunk.text_deidentified
    assert fetched.source_file_path == chunk.source_file_path


def test_db_insert_family_a_date_filter(db_path):
    """fetch_family_a_sections with date filter returns only matching rows."""
    for i, date in enumerate(["2025-01-01", "2025-03-01", "2025-06-01"]):
        chunk = FamilyAChunk(
            patient_id=_PATIENT_ID,
            template_type="clinic_visit_summary",
            session_date=date,
            section="רקע",
            text_deidentified=f"ביקור מספר {i + 1}.",
        )
        insert_family_a_chunk(db_path, chunk)

    rows = fetch_family_a_sections(
        db_path,
        patient_id=_PATIENT_ID,
        template_type="clinic_visit_summary",
        date_from="2025-02-01",
        date_to="2025-05-01",
    )

    assert len(rows) == 1
    assert rows[0].session_date == "2025-03-01"


# ---------------------------------------------------------------------------
# SQLite — TreatmentSessionChunk insert, fetch, date filter
# ---------------------------------------------------------------------------

def test_db_treatment_sessions(db_path):
    """Insert 3 TreatmentSessionChunks; date filter must return the correct count."""
    sessions = [
        TreatmentSessionChunk(
            patient_id=_PATIENT_ID,
            session_date="2025-01-07",
            session_text_deidentified="עבדנו על זיכרון שמיעתי.",
        ),
        TreatmentSessionChunk(
            patient_id=_PATIENT_ID,
            session_date="2025-01-14",
            session_text_deidentified="עבדנו על מודעות פונולוגית.",
        ),
        TreatmentSessionChunk(
            patient_id=_PATIENT_ID,
            session_date="2025-02-04",
            session_text_deidentified="עבדנו על פרגמטיקה.",
        ),
    ]

    for s in sessions:
        insert_treatment_session(db_path, s)

    # Fetch all
    all_rows = fetch_treatment_sessions(db_path, patient_id=_PATIENT_ID)
    assert len(all_rows) == 3

    # Fetch with date range — only January sessions
    jan_rows = fetch_treatment_sessions(
        db_path,
        patient_id=_PATIENT_ID,
        date_from="2025-01-01",
        date_to="2025-01-31",
    )
    assert len(jan_rows) == 2

    # Fetch February only
    feb_rows = fetch_treatment_sessions(
        db_path,
        patient_id=_PATIENT_ID,
        date_from="2025-02-01",
        date_to="2025-02-28",
    )
    assert len(feb_rows) == 1
    assert feb_rows[0].session_date == "2025-02-04"


def test_db_treatment_sessions_empty(db_path):
    """fetch_treatment_sessions returns an empty list for unknown patient."""
    rows = fetch_treatment_sessions(db_path, patient_id="nonexistent_patient")
    assert rows == []


# ---------------------------------------------------------------------------
# SQLite — patient_metadata round-trip
# ---------------------------------------------------------------------------

def test_db_patient_metadata(db_path):
    """upsert_patient_metadata and get_patient_metadata must round-trip correctly."""
    patient_id = _PATIENT_ID

    upsert_patient_metadata(db_path, patient_id, "date_of_birth", "01/05/2018")
    upsert_patient_metadata(db_path, patient_id, "hmo_name", "מכבי")
    upsert_patient_metadata(db_path, patient_id, "national_id", "123456789")

    meta = get_patient_metadata(db_path, patient_id)

    assert meta["date_of_birth"] == "01/05/2018"
    assert meta["hmo_name"] == "מכבי"
    assert meta["national_id"] == "123456789"


def test_db_patient_metadata_upsert(db_path):
    """upsert_patient_metadata overwrites an existing value correctly."""
    patient_id = _PATIENT_ID

    upsert_patient_metadata(db_path, patient_id, "hmo_name", "מכבי")
    upsert_patient_metadata(db_path, patient_id, "hmo_name", "כללית")  # overwrite

    meta = get_patient_metadata(db_path, patient_id)
    assert meta["hmo_name"] == "כללית"


def test_db_patient_metadata_unknown_patient(db_path):
    """get_patient_metadata returns an empty dict for an unknown patient_id."""
    meta = get_patient_metadata(db_path, "unknown_patient_xyz")
    assert meta == {}


# ---------------------------------------------------------------------------
# RouteDecision carries params
# ---------------------------------------------------------------------------

def test_route_decision_carries_params():
    """The RouteDecision must carry the original QueryParams object."""
    params = QueryParams(
        patient_id=_PATIENT_ID,
        intent="summarize",
        template_type="treatment_plan",
        date_from="2025-01-01",
        date_to="2025-03-31",
    )
    decision = route(params)

    assert decision.params is params
    assert decision.params.date_from == "2025-01-01"
    assert decision.params.date_to == "2025-03-31"
