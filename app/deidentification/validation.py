"""
validation.py — Pass 2 de-identification validation gate.

Three complementary checks confirm that a piece of text has been fully
de-identified before it is allowed to proceed to external services (Cohere,
Pinecone, Claude).

Public API
----------
validate_deidentified(text, reid_map) -> ValidationResult

ValidationResult.passed        — True if all checks passed
ValidationResult.failure_type  — "regex" | "reid_map" | "ner_rescan" | None
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    passed: bool
    failure_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Regex patterns for residual PII detection
# ---------------------------------------------------------------------------

_REGEX_PATTERNS: "dict[str, str]" = {
    "national_id": r"\b\d{9}\b",
    "email":       r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "phone":       r"\b0[1-9][0-9]{7,8}\b|\b\+972[1-9][0-9]{7,8}\b",
}

# Pattern that a valid de-identified token must match
_TOKEN_PATTERN = re.compile(r"^(PERSON|INST)_[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_regex_patterns(text: str) -> bool:
    """Return True (clean) if none of the known PII regex patterns match *text*."""
    for name, pattern in _REGEX_PATTERNS.items():
        if re.search(pattern, text):
            logger.warning(
                "validation: regex check failed — pattern '%s' matched in text.", name
            )
            return False
    return True


def _check_reid_map_values(text: str, reid_map: dict) -> bool:
    """Return True (clean) if no raw value from the re-id map appears in *text*.

    A hit means a substitution was missed during Pass 1.
    """
    for raw_value in reid_map.values():
        if raw_value and raw_value in text:
            logger.warning(
                "validation: reid_map check failed — a plaintext PII value was found in the text."
            )
            return False
    return True


def _check_ner_rescan(text: str) -> bool:
    """Return True (clean) if NER finds no unreplaced PER/ORG entities.

    Skipped entirely (returns True) when the NER model is not loaded.
    An entity is acceptable only if its text matches the PERSON_xxx or INST_xxx
    token format — meaning it was already de-identified in Pass 1.
    """
    from .ner import extract_entities, is_model_loaded  # noqa: PLC0415

    if not is_model_loaded():
        return True

    try:
        entities = extract_entities(text)
    except Exception as exc:  # noqa: BLE001
        logger.error("validation: NER re-scan raised an exception: %s", exc)
        # Conservative: if NER fails we cannot confirm safety — treat as failure
        return False

    for entity in entities:
        entity_text = entity.get("text", "")
        if not _TOKEN_PATTERN.match(entity_text):
            label = entity.get("label", "?")
            logger.warning(
                "validation: NER re-scan found unreplaced %s entity (token form not matched).",
                label,
            )
            return False

    return True


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------

def validate_deidentified(text: str, reid_map: dict) -> ValidationResult:
    """Run all three de-identification validation checks in order.

    Checks are applied in order: regex → reid_map → ner_rescan.
    The first failure short-circuits — subsequent checks are not run.

    Returns a ValidationResult with passed=True only when all three checks
    pass (or the NER model is not loaded, in which case ner_rescan is skipped).
    """
    if not _check_regex_patterns(text):
        return ValidationResult(passed=False, failure_type="regex")

    if not _check_reid_map_values(text, reid_map):
        return ValidationResult(passed=False, failure_type="reid_map")

    if not _check_ner_rescan(text):
        return ValidationResult(passed=False, failure_type="ner_rescan")

    return ValidationResult(passed=True)
