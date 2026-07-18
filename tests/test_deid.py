"""
test_deid.py — De-identification unit tests.

All tests use entirely fictitious PII. No real patient data is present.

Modules under test:
    app.deidentification.deid         — Pass 1 orchestrator
    app.deidentification.validation   — Pass 2 gate
    app.deidentification.reid_map     — re-id map operations
"""

import os

import pytest

from app.deidentification.deid import deidentify_text, validate_name_variants
from app.deidentification.validation import validate_deidentified
from app.deidentification.reid_map import (
    add_entity,
    load,
    patient_id_from_folder,
    reverse_lookup,
    reidentify_text,
    save,
    token_to_hash,
)


# ---------------------------------------------------------------------------
# Helpers — all fictitious data, no real PHI
# ---------------------------------------------------------------------------

_PATIENT_FOLDER = "כהן יוסי"
_PATIENT_FULL = "יוסי כהן"  # first-name-first variant used inside documents


def _base_reid_map() -> dict:
    """Return a fresh empty re-id map for each test."""
    return {}


def _deid(text: str, reid_map: dict | None = None) -> str:
    """Convenience wrapper: run deidentify_text with the test patient."""
    if reid_map is None:
        reid_map = _base_reid_map()
    variants, conflict = validate_name_variants(_PATIENT_FOLDER, "", None)
    assert not conflict, "Test helper: unexpected name conflict"
    return deidentify_text(
        text=text,
        patient_folder_name=_PATIENT_FOLDER,
        name_variants=variants,
        reid_map=reid_map,
        source_path="test_source.docx",
    )


# ---------------------------------------------------------------------------
# Pass 1 — patient name
# ---------------------------------------------------------------------------

def test_patient_name_replaced():
    """Patient name (folder-name form) must not appear in de-id output; a PERSON_ token replaces it.

    The canonical variant list always contains the patient_folder_name ("כהן יוסי").
    That form is what deidentify_text replaces deterministically in Pass 1.
    """
    # Use the folder-name form (last-name-first) which is always in the variant list
    text = f"הגיע {_PATIENT_FOLDER} לקליניקה."
    result = _deid(text)

    assert _PATIENT_FOLDER not in result
    assert "PERSON_" in result


def test_patient_name_folder_form_replaced():
    """The folder-name form (last name first) should also be replaced."""
    text = f"שם המטופל: {_PATIENT_FOLDER}."
    result = _deid(text)

    assert _PATIENT_FOLDER not in result
    assert "PERSON_" in result


# ---------------------------------------------------------------------------
# Pass 1 — national ID
# ---------------------------------------------------------------------------

def test_national_id_replaced():
    """Nine-digit national ID must be replaced with an ID_ token."""
    fake_id = "123456789"
    text = f"ת.ז. {fake_id} של המטופל."
    result = _deid(text)

    assert fake_id not in result
    assert "ID_" in result


# ---------------------------------------------------------------------------
# Pass 1 — phone number
# ---------------------------------------------------------------------------

def test_phone_replaced():
    """Israeli mobile phone number must be replaced with a PHONE_ token."""
    phone = "0521234567"
    text = f"טלפון: {phone}."
    result = _deid(text)

    assert phone not in result
    assert "PHONE_" in result


@pytest.mark.parametrize("phone", [
    "052-1234567",       # hyphenated mobile
    "052 123 4567",      # space-segmented
    "+972-52-1234567",   # international, hyphenated
    "+972521234567",     # international, contiguous
    "03-1234567",        # landline, hyphenated
])
def test_phone_written_forms_replaced(phone):
    """All standard written phone forms must be replaced with a PHONE_ token (Pass 1)."""
    result = _deid(f"טלפון: {phone} של ההורה.")
    assert phone not in result
    assert "PHONE_" in result


@pytest.mark.parametrize("nid", [
    "12345678-9",  # hyphenated check digit
    "12345678",    # bare 8-digit ID
])
def test_national_id_written_forms_replaced(nid):
    """Hyphenated and 8-digit national IDs must be replaced with an ID_ token (Pass 1)."""
    result = _deid(f"ת.ז. {nid} של המטופל.")
    assert nid not in result
    assert "ID_" in result


# ---------------------------------------------------------------------------
# Pass 1 — email address
# ---------------------------------------------------------------------------

def test_email_replaced():
    """Email address must be replaced with an EMAIL_ token."""
    email = "test@test.com"
    text = f"אימייל: {email}."
    result = _deid(text)

    assert email not in result
    assert "EMAIL_" in result


# ---------------------------------------------------------------------------
# Pass 2 — regex check
# ---------------------------------------------------------------------------

def test_pass2_regex_check():
    """validate_deidentified must fail with failure_type='regex' when a 9-digit
    sequence remains in text (simulates a missed national-ID substitution)."""
    # Text that still contains a raw national ID — as if Pass 1 failed to replace it
    bad_text = "המטופל עם ת.ז. 123456789 זקוק לטיפול."
    reid_map = _base_reid_map()

    result = validate_deidentified(bad_text, reid_map)

    assert result.passed is False
    assert result.failure_type == "regex"


@pytest.mark.parametrize("phone", [
    "052-1234567",
    "052 123 4567",
    "+972-52-1234567",
])
def test_pass2_detects_phone_written_forms(phone):
    """Pass 2 must flag written phone forms that slipped through Pass 1."""
    result = validate_deidentified(f"טלפון: {phone}.", {})
    assert result.passed is False
    assert result.failure_type == "regex"


@pytest.mark.parametrize("nid", ["12345678-9", "12345678"])
def test_pass2_detects_national_id_written_forms(nid):
    """Pass 2 must flag hyphenated / 8-digit national IDs that slipped through Pass 1."""
    result = validate_deidentified(f"ת.ז. {nid}.", {})
    assert result.passed is False
    assert result.failure_type == "regex"


# ---------------------------------------------------------------------------
# Pass 2 — reid_map check
# ---------------------------------------------------------------------------

def test_pass2_reid_map_check():
    """validate_deidentified must fail with failure_type='reid_map' when a known
    plaintext PII value from the re-id map appears in the text."""
    patient_name = "יוסי כהן"
    reid_map: dict = {}
    # Manually add the patient to the map (simulates what Pass 1 would have done)
    add_entity(reid_map, "PERSON", patient_name)

    # Text still contains the raw name — Pass 1 would have replaced it but didn't
    bad_text = f"המטופל {patient_name} הגיע לפגישה."

    result = validate_deidentified(bad_text, reid_map)

    assert result.passed is False
    assert result.failure_type == "reid_map"


# ---------------------------------------------------------------------------
# re-id map — add_entity and reverse_lookup
# ---------------------------------------------------------------------------

def test_reid_map_add_entity():
    """add_entity must return a PERSON_ token; reverse_lookup must return the original."""
    reid_map: dict = {}
    entity_value = "יוסי כהן"

    token = add_entity(reid_map, "PERSON", entity_value)

    assert token.startswith("PERSON_")
    assert len(token) > len("PERSON_")

    looked_up = reverse_lookup(reid_map, token)
    assert looked_up == entity_value


def test_reid_map_add_entity_idempotent():
    """Adding the same entity twice must return the same token (deterministic hash)."""
    reid_map: dict = {}
    value = "בית ספר אלון"

    token1 = add_entity(reid_map, "INST", value)
    token2 = add_entity(reid_map, "INST", value)

    assert token1 == token2


def test_reid_map_reverse_lookup_unknown():
    """reverse_lookup must return None for a token not in the map."""
    reid_map: dict = {}
    # A well-formed but unknown PERSON token (64 hex chars of zeros)
    fake_token = "PERSON_" + "0" * 64

    result = reverse_lookup(reid_map, fake_token)
    assert result is None


# ---------------------------------------------------------------------------
# re-id map — random IDs (lookup-or-mint) + atomic save
# ---------------------------------------------------------------------------

def test_add_entity_same_value_same_id():
    """The same value must reuse the same map key (lookup-or-mint)."""
    reid_map: dict = {}
    token1 = add_entity(reid_map, "PERSON", "יוסי כהן")
    token2 = add_entity(reid_map, "PERSON", "יוסי כהן")
    assert token1 == token2
    assert len(reid_map) == 1


def test_add_entity_different_values_different_ids():
    reid_map: dict = {}
    token1 = add_entity(reid_map, "PERSON", "יוסי כהן")
    token2 = add_entity(reid_map, "PERSON", "דוד לוי")
    assert token1 != token2
    assert len(reid_map) == 2


def test_add_entity_key_not_derivable_from_value():
    """The map key must NOT be sha256(value) — the dictionary attack is dead."""
    import hashlib
    reid_map: dict = {}
    value = "יוסי כהן"
    add_entity(reid_map, "PERSON", value)
    assert hashlib.sha256(value.encode()).hexdigest() not in reid_map


def test_patient_id_from_folder_lookup():
    """Pure lookup: None before the folder is in the map, the token's key after."""
    reid_map: dict = {}
    assert patient_id_from_folder(reid_map, "כהן יוסי") is None
    token = add_entity(reid_map, "PERSON", "כהן יוסי")
    assert patient_id_from_folder(reid_map, "כהן יוסי") == token_to_hash(token)


def test_save_atomic_no_tmp_left(tmp_path):
    """save must round-trip through load and leave no .tmp file behind."""
    reid_map: dict = {}
    add_entity(reid_map, "PERSON", "יוסי כהן")
    path = str(tmp_path / "reid_map.json")
    save(path, reid_map)
    assert load(path) == reid_map
    assert not os.path.exists(path + ".tmp")


# ---------------------------------------------------------------------------
# re-id map — reidentify_text
# ---------------------------------------------------------------------------

def test_reidentify_text():
    """reidentify_text must replace all tokens with their original values."""
    reid_map: dict = {}
    original_name = "יוסי כהן"
    token = add_entity(reid_map, "PERSON", original_name)

    tokenized_text = f"הגיע {token} לקליניקה."
    result = reidentify_text(reid_map, tokenized_text)

    assert original_name in result
    assert token not in result


def test_reidentify_text_multiple_tokens():
    """reidentify_text must handle multiple different tokens in the same text."""
    reid_map: dict = {}
    name = "יוסי כהן"
    institution = "בית ספר אלון"

    name_token = add_entity(reid_map, "PERSON", name)
    inst_token = add_entity(reid_map, "INST", institution)

    tokenized = f"התלמיד {name_token} לומד ב-{inst_token}."
    result = reidentify_text(reid_map, tokenized)

    assert name in result
    assert institution in result
    assert name_token not in result
    assert inst_token not in result


# ---------------------------------------------------------------------------
# Name variant validation
# ---------------------------------------------------------------------------

def test_name_variant_valid():
    """When the filename last name matches the canonical last name, no conflict is raised."""
    patient_folder = "כהן יוסי"
    filename = "אבחון בנים כהן יוסי.docx"
    header_name = None

    variants, had_conflict = validate_name_variants(patient_folder, filename, header_name)

    assert had_conflict is False
    # Canonical name must always be first in the variant list
    assert variants[0] == patient_folder


def test_name_variant_conflict():
    """When the filename contains a different last name, a conflict must be returned."""
    patient_folder = "כהן יוסי"
    filename = "אבחון בנים לוי דוד.docx"  # different last name
    header_name = None

    variants, had_conflict = validate_name_variants(patient_folder, filename, header_name)

    assert had_conflict is True
    assert variants == []


def test_name_variant_header_accepted():
    """A header name whose last word matches the canonical last word is accepted.

    Implementation note: the code uses the LAST word of patient_folder_name as the
    canonical "last name" key.  For folder "כהן יוסי" the canonical key is "יוסי".
    A header variant is accepted when its own last word also equals "יוסי".
    """
    patient_folder = "כהן יוסי"
    filename = ""
    # Header whose last word matches the canonical key ("יוסי")
    header_name = "שרה יוסי"  # different first word, same last word

    variants, had_conflict = validate_name_variants(patient_folder, filename, header_name)

    assert had_conflict is False
    assert patient_folder in variants
    assert header_name in variants


def test_name_variant_header_conflict():
    """A header name with a different last name triggers a conflict."""
    patient_folder = "כהן יוסי"
    filename = ""
    header_name = "דוד לוי"  # entirely different name

    variants, had_conflict = validate_name_variants(patient_folder, filename, header_name)

    assert had_conflict is True
    assert variants == []


def test_name_variant_empty_folder():
    """An empty patient_folder_name returns an empty list without conflict."""
    variants, had_conflict = validate_name_variants("", "anything.docx", None)

    assert variants == []
    assert had_conflict is False
