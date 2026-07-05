"""
adapter_a.py — Family A parser for diagnosis and clinic_visit_summary documents.

Parses .docx files with a fixed section structure and extracts:
- Header fields (name, date, date_of_birth, national_id, hmo_name)
- Section text (one entry per fixed section key)
- Domain findings (diagnosis only, from the ממצאי האבחון section)
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section and domain constants
# ---------------------------------------------------------------------------

# Exact Hebrew header text as it appears in the .docx paragraphs
SECTION_HEADERS = [
    "רקע",
    "מהלך האבחון",
    "ממצאי האבחון",
    "סיכום והמלצות",
    "תמצית אבחון",
]

# Normalised section keys (used as 'section' field in SQLite)
SECTION_KEYS = [
    "רקע",
    "מהלך_האבחון",
    "ממצאי_האבחון",
    "סיכום_והמלצות",
    "תמצית_אבחון",
]

# Mapping from header text → section key
_HEADER_TO_KEY: dict[str, str] = dict(zip(SECTION_HEADERS, SECTION_KEYS))

# 13 domain sub-headers inside ממצאי האבחון (diagnosis only)
DOMAIN_HEADERS = [
    "פרגמטיקה ותקשורת",
    "הבנת שפה",
    "הבעת שפה",
    "לקסיקון",
    "תחביר",
    "מורפולוגיה",
    "התארגנות להבעת מלל מורכב",
    "מובנות הדיבור",
    "מודעות פונולוגית",
    "זיכרון שמיעתי",
    "אורל מוטור",
    "אכילה",
    "שטף",
]

# Map sub-domains to their parent category
PARENT_DOMAINS: dict[str, str] = {
    "לקסיקון": "הבעת שפה",
    "תחביר": "הבעת שפה",
    "מורפולוגיה": "הבעת שפה",
    "התארגנות להבעת מלל מורכב": "הבעת שפה",
}

# Signature-block sentinel — clinician licence number indicator
_SIGNATURE_SENTINEL = "מ.ר."


# ---------------------------------------------------------------------------
# Template type detection
# ---------------------------------------------------------------------------

def detect_template_type(filename: str) -> Optional[str]:
    """Detect the Family A template type from the document filename.

    Parameters
    ----------
    filename:
        The basename of the .docx file (with or without extension).

    Returns
    -------
    'diagnosis' if the filename starts with 'אבחון',
    'clinic_visit_summary' if it starts with 'סיכום ביקור',
    None otherwise.
    """
    if filename.startswith("סיכום אבחון"):
        return "diagnosis"
    if filename.startswith("סיכום ביקור"):
        return "clinic_visit_summary"
    return None


# ---------------------------------------------------------------------------
# Header field extraction
# ---------------------------------------------------------------------------

def extract_header_fields(doc) -> dict:
    """Scan the first ~10 paragraphs of *doc* for known header field patterns.

    Fields extracted
    ----------------
    name           : text after 'שם:' or 'שם '
    date           : text after 'תאריך:'
    date_of_birth  : text after 'ת.ל.:'
    national_id    : text after 'ת.ז.:'
    hmo_name       : text after 'קופת חולים:' / 'קופת חולים ' / 'HMO:'

    Parameters
    ----------
    doc:
        A python-docx Document object.

    Returns
    -------
    Dict with keys: name, date, date_of_birth, national_id, hmo_name.
    All values are str or None.
    """
    result: dict[str, Optional[str]] = {
        "name": None,
        "date": None,
        "date_of_birth": None,
        "national_id": None,
        "hmo_name": None,
    }

    # Patterns: each tuple is (field_key, compiled_regex)
    # Each regex captures the value in group 1.
    patterns = [
        ("name",          re.compile(r"שם\s*[:\s]\s*(.+)")),
        ("date",          re.compile(r"תאריך\s*:\s*(.+)")),
        ("date_of_birth", re.compile(r"ת\.ל\.\s*:\s*(.+)")),
        ("national_id",   re.compile(r"ת\.ז\.\s*:\s*(.+)")),
        ("hmo_name",      re.compile(r"(?:קופת חולים|HMO)\s*[:\s]\s*(.+)", re.IGNORECASE)),
    ]

    paragraphs = doc.paragraphs[:10]
    for para in paragraphs:
        text = para.text.strip()
        if not text:
            continue
        for field_key, pattern in patterns:
            if result[field_key] is not None:
                # Already found this field
                continue
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                if value:
                    result[field_key] = value

    return result


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_family_a(
    file_path: str,
    patient_folder_name: str,
    template_type: str,
) -> dict:
    """Parse a Family A .docx document and return structured data.

    Parameters
    ----------
    file_path:
        Absolute path to the .docx file.
    patient_folder_name:
        The name of the patient's folder (used as the canonical patient
        identifier before de-identification).
    template_type:
        'diagnosis' or 'clinic_visit_summary'.

    Returns
    -------
    {
        "template_type": str,
        "patient_folder_name": str,
        "header": dict,           # from extract_header_fields
        "sections": dict,         # section_key -> concatenated paragraph text
        "domains": dict,          # domain_name -> text (diagnosis only, else {})
        "file_path": str,
    }
    """
    from docx import Document  # type: ignore

    try:
        doc = Document(file_path)
    except Exception as exc:
        logger.error("parse_family_a: failed to open document %s: %s", file_path, exc)
        raise

    header = extract_header_fields(doc)

    # Initialise all sections with empty strings (missing sections are kept as "")
    sections: dict[str, str] = {key: "" for key in SECTION_KEYS}
    domains: dict[str, str] = {}

    current_section_key: Optional[str] = None
    current_domain: Optional[str] = None
    section_lines: dict[str, list[str]] = {key: [] for key in SECTION_KEYS}
    domain_lines: dict[str, list[str]] = {}
    blocked: bool = False  # True once signature block sentinel is encountered

    for para in doc.paragraphs:
        raw_text = para.text
        stripped = raw_text.strip()

        # Empty paragraph — skip (but reset blocked flag per section only)
        if not stripped:
            continue

        # --- Signature block sentinel ---
        # If the clinician licence marker appears, stop collecting for current section.
        if _SIGNATURE_SENTINEL in stripped:
            blocked = True
            current_section_key = None
            current_domain = None
            continue

        # --- Section header detection ---
        # Use startswith() to handle colons and whitespace variants
        section_key = None
        for section_header_text, key in _HEADER_TO_KEY.items():
            if stripped.rstrip(':').startswith(section_header_text.rstrip(':')):
                section_key = key
                break

        if section_key is not None:
            current_section_key = section_key
            current_domain = None
            blocked = False  # new section resets the blocked flag
            continue

        # --- Domain header detection (inside ממצאי האבחון, diagnosis only) ---
        # Use startswith() to handle colons and whitespace variants
        if template_type == "diagnosis" and current_section_key == "ממצאי_האבחון":
            matched_domain = None
            for domain in DOMAIN_HEADERS:
                if stripped.rstrip(':').startswith(domain.rstrip(':')):
                    matched_domain = domain
                    break

            if matched_domain is not None:
                current_domain = matched_domain
                if current_domain not in domain_lines:
                    domain_lines[current_domain] = []
                continue

        # --- Content collection ---
        if blocked:
            continue

        if current_section_key is not None:
            if current_domain is not None and template_type == "diagnosis":
                # Collect into the current domain
                domain_lines[current_domain].append(stripped)
            else:
                # Collect into the current section
                section_lines[current_section_key].append(stripped)

    # Assemble section texts
    for key in SECTION_KEYS:
        section_texts = section_lines.get(key, [])
        sections[key] = "\n".join(section_texts).strip()

    # Assemble domain texts (diagnosis only)
    if template_type == "diagnosis":
        for domain_name, lines in domain_lines.items():
            domains[domain_name] = "\n".join(lines).strip()

    logger.info(
        "parse_family_a: parsed %s (%s) — sections=%d domains=%d",
        file_path,
        template_type,
        sum(1 for v in sections.values() if v),
        len(domains),
    )

    return {
        "template_type": template_type,
        "patient_folder_name": patient_folder_name,
        "header": header,
        "sections": sections,
        "domains": domains,
        "parent_domains": PARENT_DOMAINS,
        "file_path": file_path,
    }
