"""
adapter_b.py — Family B parser for treatment_plan documents.

The body is read in document order:
  Goals tables — title row 'מטרות [בתאריך] <date>', then 'domain | goal' rows;
                 one goals record per table.
  Sessions     — a line that is only a date starts a session; the lines after
                 it, up to the next date line, are its text.

All dates go through the shared date parser (app/ingestion/dates.py) and are
stored as ISO 8601 YYYY-MM-DD.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from docx.table import Table

from app.ingestion.adapter_a import _lines
from app.ingestion.dates import ParsedDate, format_date, parse_date, resolve_dates
from app.ingestion.result import ParseBlocked

logger = logging.getLogger(__name__)


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
# Goals tables (PLAN.md items 16–18; T2 decisions 2-א … 2-ה)
# ---------------------------------------------------------------------------

# 'מטרות בתאריך 27/07/2026', 'מטרות:', 'מטרות: בתאריך 2/8/26:', 'מטרות בתאריך' (undated)
_GOALS_TITLE_RE = re.compile(r"מטרות\s*:?\s*(?:בתאריך)?\s*(?P<date>.*?)\s*:?")
_UNKNOWN_TABLE_MSG = (
    "בתוכנית יש טבלה במבנה לא מוכר (מתחילה ב-'{start}') — יש להשתמש בטבלת מטרות: "
    "כותרת 'מטרות בתאריך …' ושתי עמודות, תחום ומטרה"
)
_UNDATED_TABLE_MSG = "לטבלת המטרות אין תאריך — נשמרה בתאריך {what} ({date})"
_UNDATED_TABLE_BLOCK_MSG = (
    "לטבלת המטרות אין תאריך, ואין בתוכנית תאריך תחילת טיפול או מפגש — "
    "יש לכתוב תאריך בכותרת הטבלה (למשל 'מטרות בתאריך 10/07/2026')"
)


def _cell_text(cell) -> str:
    """A cell's lines, stripped, joined with '; ' (T2 2-ג)."""
    return "; ".join(line.strip() for line in cell.text.split("\n") if line.strip())


def _is_merged(row) -> bool:
    """True when the whole row is one merged cell."""
    cells = row.cells
    return len(cells) > 1 and all(c._tc is cells[0]._tc for c in cells)


def read_goals_table(table: Table) -> tuple[Optional[ParsedDate], str]:
    """(title date, or None when undated; record text) of a goals table.

    Text: one line per domain, 'domain: goal1; goal2' (item 16). A row with a
    blank domain continues the domain above; empty rows are skipped; a domain
    without a goal, or a goal with no domain above it, is a line of its own.
    Raises ParseBlocked for any table that is not a 2-column goals table (2-א),
    DateError for an impossible title date.
    """
    first = table.rows[0].cells[0].text.strip()
    unknown = ParseBlocked(_UNKNOWN_TABLE_MSG.format(start=first[:30]))
    title = _GOALS_TITLE_RE.fullmatch(first)
    if title is None or len(table.columns) != 2 or any(_is_merged(r) for r in table.rows[1:]):
        raise unknown
    title_date = parse_date(title["date"]) if title["date"] else None
    if title["date"] and title_date is None:
        raise unknown

    entries: list[tuple[str, list[str]]] = []          # (domain, goals), table order
    for row in table.rows[1:]:
        domain, goal = (_cell_text(c) for c in row.cells)
        if not domain and not goal:
            continue
        if domain or not entries:
            entries.append((domain, []))
        if goal:
            entries[-1][1].append(goal)

    lines = [
        f"{domain}: {'; '.join(goals)}" if domain and goals else domain or "; ".join(goals)
        for domain, goals in entries
    ]
    return title_date, "\n".join(lines)


def _undated_table_date(start: Optional[date], first_session: Optional[date]) -> tuple[date, str]:
    """Date + warning for a goals table with no date in its title (item 18, T2 2-ד)."""
    if start is not None:
        return start, _UNDATED_TABLE_MSG.format(what="תחילת הטיפול", date=format_date(start))
    if first_session is not None:
        return first_session, _UNDATED_TABLE_MSG.format(
            what="המפגש הראשון", date=format_date(first_session))
    raise ParseBlocked(_UNDATED_TABLE_BLOCK_MSG)


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
        "warnings": list[str],    # Hebrew, shown in the load report
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
    # Body, in document order: goals tables and sessions
    # ------------------------------------------------------------------
    # Every written date (session lines, table titles) goes into ONE sequence in
    # document order, so a missing year comes from the date written just before
    # it (20b, T2 1-א).
    written: list[ParsedDate] = []
    sessions: list[tuple[int, list[str]]] = []      # (index in written, text lines)
    tables: list[tuple[Optional[int], str]] = []    # (index in written, None = undated; text)
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            title_date, text = read_goals_table(block)
            if title_date is not None:
                written.append(title_date)
            if text:
                tables.append((len(written) - 1 if title_date is not None else None, text))
            continue
        # Every line on its own (Shift+Enter, T2 1-ג); a line that is only a date
        # starts a session. Lines before the first date are header material
        # (item 15); a table between sessions does not end the session around it.
        for line in _lines([block]):
            parsed = parse_date(line)    # an impossible date raises DateError → blocked
            if parsed is not None:
                written.append(parsed)
                sessions.append((len(written) - 1, []))
            elif sessions:
                sessions[-1][1].append(line)

    # Future / before-start warnings (21a).
    resolved = resolve_dates(written, start=start)
    warnings += resolved.warnings
    dates = resolved.dates

    session_blocks = [
        {"session_date": dates[i].isoformat(), "session_text": "\n".join(text)}
        for i, text in sessions
        if text
    ]

    first_session = dates[sessions[0][0]] if sessions else None
    goals_rows: list[dict] = []
    for i, text in tables:
        if i is not None:
            table_date = dates[i]
        else:
            table_date, warning = _undated_table_date(start, first_session)
            warnings.append(warning)
        goals_rows.append({"session_date": table_date.isoformat(), "goals_text": text})

    # Edge case: no date markers found anywhere in the free-text area
    if not sessions and doc.paragraphs:
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
