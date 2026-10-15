"""
adapter_b.py — Family B parser for treatment_plan documents.

Parses .docx files that contain:
  Layer 1 — A goals table (doc.tables[0]): rows of (session_date, goals_text)
  Layer 2 — Session summaries: a line that is only a date starts a session;
             the lines after it, up to the next date line, are its text.

Session dates and the treatment start date go through the shared date parser
(app/ingestion/dates.py) and are stored as ISO 8601 YYYY-MM-DD. Goals-table
dates still use the old helpers below until T2 part 2 replaces Layer 1.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from app.ingestion.adapter_a import _lines
from app.ingestion.dates import ParsedDate, parse_date, resolve_dates
from app.ingestion.result import ParseBlocked

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Date format registry (tried in order)
# ---------------------------------------------------------------------------

# Each entry: (strptime_format, compiled_regex_for_quick_pre-check)
LAYER2_DATE_FORMATS = [
    ("%d/%m/%Y", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%d.%m.%Y", re.compile(r"^\d{2}\.\d{2}\.\d{4}$")),
    ("%d.%m.%Y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")),
]


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

def parse_layer2_date(line: str) -> Optional[str]:
    """Try to parse *line* as a session-boundary date.

    Formats are tried in the order defined by LAYER2_DATE_FORMATS.
    The first successful parse wins and is returned as an ISO 8601 string
    (YYYY-MM-DD).  Returns None if no format matches.

    Parameters
    ----------
    line:
        A single stripped line of text from the document.

    Returns
    -------
    ISO date string ('YYYY-MM-DD') or None.
    """
    stripped = line.strip()
    for fmt, pattern in LAYER2_DATE_FORMATS:
        if pattern.match(stripped):
            try:
                parsed: date = datetime.strptime(stripped, fmt).date()
                return parsed.isoformat()
            except ValueError:
                # strptime failed despite regex match (e.g. month=13) — try next
                continue
    return None


def _normalize_table_date(raw: str) -> Optional[str]:
    """Attempt to parse a table cell value as a date and return ISO 8601.

    Uses the same format list as parse_layer2_date.  Returns the original
    string unchanged if no format matches (best-effort — we do not block on
    unparseable table dates).
    """
    if not raw or not raw.strip():
        return None
    iso = parse_layer2_date(raw.strip())
    if iso:
        return iso
    # Return raw value as fallback — caller handles None from table cell
    return raw.strip() or None


# ---------------------------------------------------------------------------
# Header extraction
# ---------------------------------------------------------------------------

def extract_treatment_header(doc) -> dict:
    """Extract file-level header fields from the first paragraphs of *doc*.

    Fields
    ------
    name           : text after 'שם:' / 'שם '
    date_of_birth  : text after 'ת.ל.:'
    start_date     : text after 'תאריך התחלת הטיפול:' (raw — see resolve_start_date)
    main_goal      : text after 'מטרת על:'
    notes          : text after 'הערות:'

    Parameters
    ----------
    doc:
        A python-docx Document object.

    Returns
    -------
    Dict with the five keys above.  All values are str or None.
    """
    result: dict[str, Optional[str]] = {
        "name": None,
        "date_of_birth": None,
        "start_date": None,
        "main_goal": None,
        "notes": None,
    }

    patterns = [
        ("name",          re.compile(r"שם\s*[:\s]\s*(.+)")),
        ("date_of_birth", re.compile(r"ת\.ל\.\s*:\s*(.+)")),
        ("start_date",    re.compile(r"תאריך התחלת הטיפול\s*:\s*(.+)")),
        ("main_goal",     re.compile(r"מטרת על\s*:\s*(.+)")),
        ("notes",         re.compile(r"הערות\s*:\s*(.+)")),
    ]

    # Header lives in the first 15 paragraphs; each may hold several lines (T2 1-ג).
    for text in _lines(doc.paragraphs[:15]):
        for field_key, pattern in patterns:
            if result[field_key] is not None:
                continue
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                if value:
                    result[field_key] = value

    return result


_START_DATE_MSG = (
    "תאריך התחלת הטיפול '{raw}' אינו תאריך מלא — "
    "יש לכתוב תאריך כולל שנה (למשל 10/07/2026)"
)


def resolve_start_date(raw: Optional[str]) -> tuple[Optional[date], list[str]]:
    """The treatment start date from its header text, plus its warnings (T2 1-ב).

    Empty → no start date. Otherwise it must be a full date incl. year:
    anything else → ParseBlocked; an impossible date → DateError (both blocked).
    """
    if not raw:
        return None, []
    parsed = parse_date(raw)
    if parsed is None or parsed.year is None:
        raise ParseBlocked(_START_DATE_MSG.format(raw=raw))
    resolved = resolve_dates([parsed])
    return resolved.dates[0], resolved.warnings


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_treatment_plan(file_path: str, patient_folder_name: str) -> dict:
    """Parse a Family B (treatment_plan) .docx document.

    Parameters
    ----------
    file_path:
        Absolute path to the .docx file.
    patient_folder_name:
        The name of the patient's folder.

    Returns
    -------
    {
        "template_type": "treatment_plan",
        "patient_folder_name": str,
        "header": dict,
        "goals_rows": list of {"session_date": str, "goals_text": str},
        "session_blocks": list of {"session_date": str, "session_text": str},
        "file_path": str,
    }
    """
    from docx import Document  # type: ignore

    try:
        doc = Document(file_path)
    except Exception as exc:
        logger.error("parse_treatment_plan: failed to open document %s: %s", file_path, exc)
        raise

    header = extract_treatment_header(doc)
    start, warnings = resolve_start_date(header["start_date"])
    header["start_date"] = start.isoformat() if start else None

    # ------------------------------------------------------------------
    # Layer 1 — Goals table (doc.tables[0])
    # ------------------------------------------------------------------
    goals_rows: list[dict] = []

    if doc.tables:
        table = doc.tables[0]
        for row in table.rows:
            cells = row.cells
            if len(cells) < 2:
                continue
            raw_date = cells[0].text.strip()
            goals_text = cells[1].text.strip()

            if not raw_date and not goals_text:
                # Blank row — skip
                continue

            iso_date = _normalize_table_date(raw_date)
            if iso_date and goals_text:
                goals_rows.append({
                    "session_date": iso_date,
                    "goals_text": goals_text,
                })
            elif goals_text:
                # Date missing or unparseable — store with empty date
                logger.warning(
                    "parse_treatment_plan: goals table row has no parseable date "
                    "(raw=%r) in %s — skipping row",
                    raw_date,
                    file_path,
                )

    # ------------------------------------------------------------------
    # Layer 2 — Session summaries
    # ------------------------------------------------------------------
    # doc.paragraphs holds body paragraphs only (not table cells). Every line is
    # handled on its own (Shift+Enter, T2 1-ג); a line that is only a date starts
    # a session. Lines before the first date are header material (item 15).
    lines = _lines(doc.paragraphs)
    written: list[ParsedDate] = []
    texts: list[list[str]] = []
    for line in lines:
        parsed = parse_date(line)        # an impossible date raises DateError → blocked
        if parsed is not None:
            written.append(parsed)
            texts.append([])
        elif texts:
            texts[-1].append(line)

    # Missing years inferred in document order, start date as the reference
    # (20b, T2 1-א); future / before-start warnings (21a).
    resolved = resolve_dates(written, start=start)
    warnings += resolved.warnings
    session_blocks = [
        {"session_date": d.isoformat(), "session_text": "\n".join(text)}
        for d, text in zip(resolved.dates, texts)
        if text
    ]

    # Edge case: no date markers found anywhere in the free-text area
    if not written and doc.paragraphs:
        fallback_date = header.get("start_date") or datetime.today().date().isoformat()
        logger.warning(
            "parse_treatment_plan: no session-date markers found in Layer 2 of %s. "
            "Treating all text as a single block with session_date=%s",
            file_path,
            fallback_date,
        )
        all_lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if all_lines:
            session_blocks.append({
                "session_date": fallback_date,
                "session_text": "\n".join(all_lines).strip(),
            })

    logger.info(
        "parse_treatment_plan: parsed %s — goals_rows=%d session_blocks=%d",
        file_path,
        len(goals_rows),
        len(session_blocks),
    )

    return {
        "template_type": "treatment_plan",
        "patient_folder_name": patient_folder_name,
        "header": header,
        "goals_rows": goals_rows,
        "session_blocks": session_blocks,
        "file_path": file_path,
        "warnings": warnings,
    }
