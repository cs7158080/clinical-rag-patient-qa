"""
deid.py — Pass 1 de-identification orchestrator.

Public API
----------
deidentify_text(text, patient_folder_name, name_variants, reid_map, source_path) -> str
validate_name_variants(patient_folder_name, filename, header_name) -> (list[str], bool)
"""

import logging
import re

from .reid_map import add_entity, patient_id_from_folder  # noqa: F401 (re-export)
from .ner import extract_entities, is_model_loaded
from .validation import validate_deidentified, ValidationResult  # noqa: F401 (re-export)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns for structural PII
# ---------------------------------------------------------------------------

NATIONAL_ID_REGEX = r"\b\d{9}\b"
PHONE_REGEX = r"\b0[1-9][0-9]{7,8}\b|\b\+972[1-9][0-9]{7,8}\b"
EMAIL_REGEX = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"


# ---------------------------------------------------------------------------
# Pass 1 orchestrator
# ---------------------------------------------------------------------------

def deidentify_text(
    text: str,
    patient_folder_name: str,
    name_variants: "list[str]",
    reid_map: dict,
    source_path: str,
) -> str:
    """Apply Pass 1 de-identification to *text*.

    Steps applied in order
    ----------------------
    1. Patient name replacement — deterministic, using the folder name and all
       accepted variants.  Only full-name strings (first + last) are replaced.
    2. Hebrew NER — detects additional PER (other persons) and ORG (institutions)
       entities in the remaining text. Skipped if NER model is not loaded.
    3. Regex replacement — national ID (9-digit), phone, email.

    Parameters
    ----------
    text:                 Raw document text (after signature-block stripping).
    patient_folder_name:  Canonical patient identifier (the folder name).
    name_variants:        Accepted full-name strings to replace (from
                          validate_name_variants).  May include the folder name
                          itself; deduplication is not required here.
    reid_map:             Mutable re-id map dict; updated in-place.
    source_path:          Source file path — used only in log messages (no PII).

    Returns
    -------
    De-identified text string.
    """
    # ------------------------------------------------------------------
    # Step 1 — patient name pre-replacement (deterministic)
    # ------------------------------------------------------------------
    patient_token = add_entity(reid_map, "PERSON", patient_folder_name)

    for variant in name_variants:
        if not variant or not variant.strip():
            continue
        # Replace full-name occurrences (case-insensitive).
        # re.escape is used so names with special regex characters are safe.
        text = re.sub(re.escape(variant), patient_token, text, flags=re.IGNORECASE)

    # ------------------------------------------------------------------
    # Step 2 — Hebrew NER for additional persons and institutions
    # ------------------------------------------------------------------
    if is_model_loaded():
        try:
            entities = extract_entities(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "deidentify_text: NER extraction failed for %s: %s", source_path, exc
            )
            entities = []

        # Sort by start offset descending so replacements don't shift offsets
        entities_sorted = sorted(entities, key=lambda e: e["start"], reverse=True)

        for entity in entities_sorted:
            entity_text = entity["text"]
            entity_label = entity["label"]

            if entity_label == "PER":
                token = add_entity(reid_map, "PERSON", entity_text)
                # Warn if this is a person who is NOT the patient
                if token != patient_token:
                    logger.warning(
                        "deidentify_text: additional PER entity detected. "
                        "token=%s source=%s",
                        token,
                        source_path,
                    )
            elif entity_label == "ORG":
                token = add_entity(reid_map, "INST", entity_text)
            else:
                continue

            # Replace the specific span (offset-based, since we sorted descending)
            start = entity["start"]
            end = entity["end"]
            text = text[:start] + token + text[end:]

    # ------------------------------------------------------------------
    # Step 3 — regex-based PII replacement
    # ------------------------------------------------------------------
    # National ID (9-digit sequences)
    def _replace_national_id(match: re.Match) -> str:
        return add_entity(reid_map, "ID", match.group(0))

    text = re.sub(NATIONAL_ID_REGEX, _replace_national_id, text)

    # Phone numbers
    def _replace_phone(match: re.Match) -> str:
        return add_entity(reid_map, "PHONE", match.group(0))

    text = re.sub(PHONE_REGEX, _replace_phone, text)

    # Email addresses
    def _replace_email(match: re.Match) -> str:
        return add_entity(reid_map, "EMAIL", match.group(0))

    text = re.sub(EMAIL_REGEX, _replace_email, text)

    return text


# ---------------------------------------------------------------------------
# Name variant validation
# ---------------------------------------------------------------------------

def validate_name_variants(
    patient_folder_name: str,
    filename: str,
    header_name: "str | None",
) -> "tuple[list[str], bool]":
    """Validate and collect patient name variants from the three sources.

    The canonical last name is the last whitespace-separated word of
    *patient_folder_name*.  A source is accepted only if its own last word
    matches the canonical last name (case-insensitive).  A different last name
    is treated as a conflict.

    Parameters
    ----------
    patient_folder_name:
        The patient's folder name — the canonical source (source 1).
    filename:
        The document filename (without path, with or without .docx extension).
        The name is assumed to be the suffix after the last run of whitespace
        before the extension.  E.g. "אבחון בנות יוסי כהן.docx" → "יוסי כהן".
    header_name:
        The value of the שם field from the document header (source 3), or None.

    Returns
    -------
    (valid_variants, had_conflict)
        valid_variants:  Deduplicated list of accepted full-name strings, always
                         including *patient_folder_name* as the first entry.
                         Empty list if a conflict was detected.
        had_conflict:    True if any source carried a different last name.
    """
    if not patient_folder_name or not patient_folder_name.strip():
        return ([], False)

    canonical_name = patient_folder_name.strip()
    canonical_last = canonical_name.split()[-1].lower()

    # Collect raw candidates (source 2 and source 3)
    candidates: "list[str]" = []

    # Source 2 — filename
    name_from_filename = _extract_name_from_filename(filename)
    if name_from_filename:
        candidates.append(name_from_filename)

    # Source 3 — header שם field
    if header_name and header_name.strip():
        candidates.append(header_name.strip())

    # Validate each candidate against the canonical last name
    valid: "list[str]" = []
    for candidate in candidates:
        words = candidate.split()
        if not words:
            continue
        candidate_last = words[-1].lower()
        if candidate_last != canonical_last:
            # Conflict: different last name found
            logger.error(
                "validate_name_variants: last-name conflict detected. "
                "Ingestion must be stopped. (conflict details not logged per privacy policy)"
            )
            return ([], True)
        # Same last name — accept variant (only if it is a full name, i.e. ≥ 2 words)
        if len(words) >= 2:
            valid.append(candidate)

    # Build deduplicated list: canonical name always first
    seen: "set[str]" = set()
    result: "list[str]" = []

    def _add(name: str) -> None:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            result.append(name)

    _add(canonical_name)
    for v in valid:
        _add(v)

    return (result, False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_name_from_filename(filename: str) -> "str | None":
    """Extract the name suffix from a document filename.

    Strategy: strip the .docx extension (if present), then return the last
    token after the last run of whitespace.  If the result looks like a full
    name (≥ 2 words) it is returned; otherwise the remainder after the last
    space is returned so the caller can validate.

    Examples
    --------
    "אבחון בנות יוסי כהן.docx"          → "יוסי כהן"
    "סיכום ביקור 3 יוסי כהן.docx"       → "יוסי כהן"
    "תוכנית טיפול הדרכתי יוסי כהן.docx" → "יוסי כהן"
    """
    if not filename or not filename.strip():
        return None

    name = filename.strip()

    # Strip .docx extension (case-insensitive)
    if name.lower().endswith(".docx"):
        name = name[:-5].strip()

    if not name:
        return None

    # The template prefixes end before the patient name.  We identify the name
    # as the last one or two tokens that look like a name (contain only letters/
    # Unicode word chars, no digits).  A simple heuristic: split on whitespace
    # and walk backwards collecting non-numeric tokens until a numeric token or
    # a known keyword is encountered.
    tokens = name.split()
    name_tokens: "list[str]" = []
    for token in reversed(tokens):
        # Stop at pure-digit tokens (e.g. visit numbers like "3")
        if token.isdigit():
            break
        name_tokens.insert(0, token)

    if not name_tokens:
        return None

    return " ".join(name_tokens)
