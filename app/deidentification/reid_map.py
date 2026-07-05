"""
reid_map.py — Re-identification map operations.

The re-id map is a plain dict: { sha256_hex: original_value }.
All SHA256 hashes are computed as hashlib.sha256(value.encode()).hexdigest().
"""

import hashlib
import json
import logging
import os
import re

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

    Creates parent directories if they do not exist.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(reid_map, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entity management
# ---------------------------------------------------------------------------

def add_entity(reid_map: dict, entity_type: str, value: str) -> str:
    """Add an entity to the re-id map and return its token.

    entity_type: 'PERSON' | 'ID' | 'INST' | 'PHONE' | 'EMAIL'

    The same value always produces the same token (deterministic).
    If the entity already exists in the map, the entry is not duplicated.
    Returns the token string, e.g. 'PERSON_a3f7b2...'.
    """
    hash_hex = hashlib.sha256(value.encode()).hexdigest()
    token = f"{entity_type}_{hash_hex}"
    reid_map[hash_hex] = value
    return token


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

def patient_id_from_folder(folder_name: str) -> str:
    """Return the anonymous patient_id derived from the patient folder name.

    patient_id = sha256(folder_name) — identical to the hash embedded in the
    PERSON token for the patient, so the re-id map can resolve both.
    """
    return hashlib.sha256(folder_name.encode()).hexdigest()
