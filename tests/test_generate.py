"""
test_generate.py — Smoke tests for the generate clinic_visit_summary feature.

These tests require:
  - ANTHROPIC_API_KEY environment variable (skipped otherwise)
  - A live Anthropic API connection

The tests use a minimal synthetic setup — no real PHI.
"""

import os
import pytest

from app.storage.db import (
    init_db,
    insert_treatment_session,
    insert_treatment_session,
    upsert_patient_metadata,
)
from app.storage.models import TreatmentSessionChunk
from app.deidentification.reid_map import add_entity


# ---------------------------------------------------------------------------
# Skip guard — all tests in this module require an API key
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping generate smoke tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_patient(db_path: str, reid_map: dict) -> tuple[str, str]:
    """Insert a minimal synthetic patient + one session into the test DB.

    Returns (patient_id, session_date).
    """
    patient_folder = "כהן יוסי"  # fictitious
    patient_id = add_entity(reid_map, "PERSON", patient_folder)
    # patient_id in DB = sha256(patient_folder); use the token hash portion
    import hashlib
    pid = hashlib.sha256(patient_folder.encode()).hexdigest()

    session_date = "2025-02-11"

    # Insert a synthetic (de-identified) session
    session = TreatmentSessionChunk(
        patient_id=pid,
        session_date=session_date,
        session_text_deidentified=(
            "עבדנו על זיכרון שמיעתי — סדרות של מספרים. "
            "התגובה הייתה טובה; הצליח לחזור על ארבעה פריטים בסדר."
        ),
        source_file_path=None,
    )
    insert_treatment_session(db_path, session)

    # Insert minimal patient metadata
    upsert_patient_metadata(db_path, pid, "date_of_birth", "01/05/2018")
    upsert_patient_metadata(db_path, pid, "hmo_name", "מכבי")
    upsert_patient_metadata(db_path, pid, "national_id", "123456789")

    return pid, session_date


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY",
)
def test_generate_summary_smoke(tmp_path, config):
    """Smoke test: run_generate_summary completes without raising an exception.

    Asserts:
    - The return value is either a file path ending with '.docx'
      or a user-facing error string (not an exception).
    """
    db_path = str(tmp_path / "clinical_rag.db")
    init_db(db_path)

    reid_map: dict = {}
    patient_id, session_date = _make_test_patient(db_path, reid_map)

    # Save a temporary reid_map file so the generate module can load it
    import json
    reid_map_path = str(tmp_path / "reid_map.json")
    with open(reid_map_path, "w", encoding="utf-8") as fh:
        json.dump(reid_map, fh, ensure_ascii=False)

    # The generate feature writes files to the patient folder.
    # We redirect that folder to tmp_path so no real data is touched.
    patients_root = str(tmp_path / "clients")
    os.makedirs(patients_root, exist_ok=True)

    # Patch config to point to our temp dirs
    config.data_dir = str(tmp_path)
    config.patients_root = patients_root

    try:
        from app.generation.summary_generator import run_generate_summary
        result = run_generate_summary(
            patient_id=patient_id,
            session_date=session_date,
            config=config,
            db_path=db_path,
            reid_map_path=reid_map_path,
        )
    except Exception as exc:
        pytest.fail(
            f"run_generate_summary raised an unexpected exception: {exc!r}"
        )

    # Result must be a string — either a .docx file path or a user-facing error message
    assert isinstance(result, str), f"Expected str result, got {type(result)}"

    # If it looks like a file path it must end with .docx
    if result.endswith(".docx"):
        assert os.path.isfile(result), f"File path returned but file does not exist: {result}"
    else:
        # It is a user-facing error message — that is acceptable for a smoke test
        # (e.g. "לא נמצאה פגישה בתאריך זה" or similar).
        assert len(result) > 0, "Error result must not be an empty string"
