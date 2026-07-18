"""
reid_map.py — Re-identification map operations.

The re-id map is a plain dict: { 64_hex_key: original_value }.
Keys are random (secrets.token_hex(32)) — NOT derivable from values.
"""

import json
import logging
import os
import re
import secrets

logger = logging.getLogger(__name__)

# Token prefixes used throughout the system
TOKEN_PREFIXES = ["PERSON_", "ID_", "INST_", "PHONE_", "EMAIL_"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load(path: str) -> dict:
    """Load the re-id map from a JSON file.

    Returns an empty dict if the file does not exist.
    """
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save(path: str, reid_map: dict) -> None:
    """Persist the re-id map to a JSON file with pretty indentation.

    Atomic: writes to <path>.tmp then os.replace, so a crash mid-write can
    never leave a corrupt map (random keys are not recomputable — corruption
    would force a full reset). os.replace is atomic on Windows too.
    Creates parent directories if they do not exist.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(reid_map, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Entity management
# ---------------------------------------------------------------------------

def add_entity(reid_map: dict, entity_type: str, value: str) -> str:
    """Add an entity to the re-id map and return its token.

    entity_type: 'PERSON' | 'ID' | 'INST' | 'PHONE' | 'EMAIL'

    Lookup-or-mint: if *value* already has a key in the map, that key is
    reused (idempotency comes from the map, not from a hash function);
    otherwise a fresh random 64-hex key is minted via secrets.token_hex(32).
    Keys are NOT derivable from values — a dictionary attack on the keys is
    impossible, and losing the map means keys cannot be recomputed.
    Returns the token string, e.g. 'PERSON_a3f7b2...'.
    """
    for hash_hex, existing_value in reid_map.items():
        if existing_value == value:
            return f"{entity_type}_{hash_hex}"
    hash_hex = secrets.token_hex(32)
    reid_map[hash_hex] = value
    return f"{entity_type}_{hash_hex}"


def token_to_hash(token: str) -> "str | None":
    """Extract the SHA256 hex hash from a token string.

    E.g. 'PERSON_abc123...' (64 hex chars) → 'abc123...'
    Returns None if the token format is invalid.
    """
    for prefix in TOKEN_PREFIXES:
        if token.startswith(prefix):
            candidate = token[len(prefix):]
            # A valid SHA256 hex string is exactly 64 lowercase hex characters
            if re.fullmatch(r"[0-9a-f]{64}", candidate):
                return candidate
    return None


def reverse_lookup(reid_map: dict, token: str) -> "str | None":
    """Look up the original value for a token.

    Returns the original string if found, otherwise None.
    """
    h = token_to_hash(token)
    if h is None:
        return None
    return reid_map.get(h)


# ---------------------------------------------------------------------------
# Re-identification of text
# ---------------------------------------------------------------------------

def reidentify_text(reid_map: dict, text: str) -> str:
    """Replace all known PII tokens in *text* with their original values.

    For each token prefix the function uses a regex to find all occurrences
    of the form PREFIX[0-9a-f]{64}.  Each match is resolved via reverse_lookup.
    If the token is not found in reid_map a WARNING is logged (token only, not
    the original value) and the token is left in place.
    """
    for prefix in TOKEN_PREFIXES:
        pattern = re.compile(rf"{re.escape(prefix)}[0-9a-f]{{64}}")
        matches = pattern.findall(text)
        for match in matches:
            original = reverse_lookup(reid_map, match)
            if original is not None:
                text = text.replace(match, original)
            else:
                logger.warning(
                    "reidentify_text: token not found in reid_map — leaving in place. token=%s",
                    match,
                )
    return text


# ---------------------------------------------------------------------------
# Patient ID helpers
# ---------------------------------------------------------------------------

def patient_id_from_folder(reid_map: dict, folder_name: str) -> "str | None":
    """Return the anonymous patient_id for a patient folder name.

    The patient_id is the reid_map key holding the folder name — identical to
    the hash embedded in the patient's PERSON token, so the map resolves both.
    Returns None when the folder is not in the map yet (callers that need to
    create it should use add_entity + token_to_hash instead).
    """
    for hash_hex, value in reid_map.items():
        if value == folder_name:
            return hash_hex
    return None
