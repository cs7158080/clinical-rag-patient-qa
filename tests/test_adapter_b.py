"""Tests for the treatment-plan parser (PLAN.md items 15–24a, section 7 T2)."""

import pytest

from app.ingestion.adapter_b import parse_treatment_plan
from app.ingestion.dates import DateError
from app.ingestion.result import ParseBlocked
from tests.fixtures.docx_factory import Table, write_docx

_PATIENT = "ישראל ישראלי"


def _plan(tmp_path, start: str, body: list[str]) -> str:
    """A plan with the usual header (start-date value = *start*) followed by *body*."""
    return str(write_docx(tmp_path / f"תוכנית טיפול {_PATIENT}.docx", [
        'בס"ד', "תוכנית טיפולית לטיפול בדגש הדרכתי", f"שם: {_PATIENT}",
        "ת.ל.: 01/01/2020", f"תאריך התחלת הטיפול: {start}", *body,
    ]))


def _parse(path):
    return parse_treatment_plan(path, _PATIENT)


def test_session_dates_go_through_the_shared_date_parser(tmp_path):    # 20a, 20b, 1-א, 1-ג
    parsed = _parse(_plan(tmp_path, "01/11/2025", [
        "15/11/25", "מפגש ראשון.",                    # 2-digit year
        "20/12", "מפגש שני.",                         # no year → previous date's year
        "05/01\nמפגש שלישי, באותה פסקה.",            # Shift+Enter; month < previous → +1
    ]))

    assert parsed["header"]["start_date"] == "2025-11-01"
    assert parsed["session_blocks"] == [
        {"session_date": "2025-11-15", "session_text": "מפגש ראשון."},
        {"session_date": "2025-12-20", "session_text": "מפגש שני."},
        {"session_date": "2026-01-05", "session_text": "מפגש שלישי, באותה פסקה."},
    ]
    assert parsed["warnings"] == [
        "התאריך '20/12' נכתב בלי שנה — הונחה השנה 2025",
        "התאריך '05/01' נכתב בלי שנה — הונחה השנה 2026",
    ]


def test_first_yearless_session_takes_the_year_from_the_start_date(tmp_path):  # 20b rule 2
    parsed = _parse(_plan(tmp_path, "01/02/2026", ["20/05", "מפגש."]))
    assert parsed["session_blocks"][0]["session_date"] == "2026-05-20"


def test_free_text_plan_without_start_date(tmp_path):                  # item 15, 20b rule 3
    path = str(write_docx(tmp_path / f"תוכנית טיפול {_PATIENT}.docx", [
        'בס"ד', _PATIENT, "הערה לפני המפגש הראשון.",
        "14/7", "מפגש.", "22/07/2026", "עוד מפגש.",
    ]))
    parsed = _parse(path)

    assert parsed["header"]["start_date"] is None
    assert [b["session_date"] for b in parsed["session_blocks"]] == ["2026-07-14", "2026-07-22"]
    assert parsed["session_blocks"][0]["session_text"] == "מפגש."   # line before 1st date ignored


def test_future_start_and_session_before_start_are_warned(tmp_path):   # 21a, 1-ב
    parsed = _parse(_plan(tmp_path, "01/01/2099", ["01/01/2026", "מפגש."]))
    assert parsed["warnings"] == [
        "התאריך '01/01/2099' עתידי — כדאי לבדוק",
        "התאריך '01/01/2026' מוקדם מתאריך תחילת הטיפול (01/01/2099) — כדאי לבדוק",
    ]


def test_empty_start_date_is_no_start_date(tmp_path):                  # 1-ב
    parsed = _parse(_plan(tmp_path, "", ["01/03/2026", "מפגש."]))
    assert parsed["header"]["start_date"] is None
    assert parsed["warnings"] == []


@pytest.mark.parametrize("written", ["ספטמבר 2025", "10/7"])           # 1-ב
def test_start_date_that_is_not_a_full_date_blocks(tmp_path, written):
    with pytest.raises(ParseBlocked, match="תאריך התחלת הטיפול"):
        _parse(_plan(tmp_path, written, ["01/03/2026", "מפגש."]))


@pytest.mark.parametrize("start, session", [
    ("31/13/2026", "01/03/2026"),   # impossible start date (1-ב)
    ("", "32/01/2026"),             # impossible session date (24a-1)
    ("", "20/05"),                  # no year and no reference date (20b)
])
def test_impossible_or_unresolvable_dates_block(tmp_path, start, session):
    with pytest.raises(DateError):
        _parse(_plan(tmp_path, start, [session, "מפגש."]))


def test_goals_table_becomes_one_record_with_a_line_per_domain(tmp_path):   # 16, 2-ג
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        Table([
            ["מטרות בתאריך 05/03/2026"],
            ["קריאה ", "ידע אותיות"],
            ["", "קוד קריאה\nצירופים"],      # blank domain = continuation; lines in a cell
            ["", ""],                           # empty row skipped
            ["מודעות פונולוגית", ""],          # domain without a goal
        ]),
        "05/03/2026", "מפגש.",
    ]))

    assert parsed["goals_rows"] == [{
        "session_date": "2026-03-05",
        "goals_text": "קריאה: ידע אותיות; קוד קריאה; צירופים\nמודעות פונולוגית",
    }]
    assert parsed["warnings"] == []


def test_each_table_is_a_record_and_its_title_date_joins_the_sequence(tmp_path):  # 17, 2-ב, 1-א
    parsed = _parse(_plan(tmp_path, "01/07/2026", [
        Table([["מטרות בתאריך 20/07"], ["תחביר", "משפט פשוט"]]),     # no year → start's year
        "03/08/2026", "מפגש.",
        Table([["מטרות: בתאריך 2/8/26:"], ["תחביר", "משפט מורכב"]]),
        "המשך המפגש.",                     # a table between sessions does not end the session
        "12/08", "מפגש.",                  # no year → previous date (the table title)
    ]))

    assert parsed["goals_rows"] == [
        {"session_date": "2026-07-20", "goals_text": "תחביר: משפט פשוט"},
        {"session_date": "2026-08-02", "goals_text": "תחביר: משפט מורכב"},
    ]
    assert parsed["session_blocks"] == [
        {"session_date": "2026-08-03", "session_text": "מפגש.\nהמשך המפגש."},
        {"session_date": "2026-08-12", "session_text": "מפגש."},
    ]
    assert parsed["warnings"] == [
        "התאריך '20/07' נכתב בלי שנה — הונחה השנה 2026",
        "התאריך '12/08' נכתב בלי שנה — הונחה השנה 2026",
    ]


@pytest.mark.parametrize("start, expected_date, warning", [                  # 18, 2-ד
    ("01/03/2026", "2026-03-01",
     "לטבלת המטרות אין תאריך — נשמרה בתאריך תחילת הטיפול (01/03/2026)"),
    ("", "2026-03-10",
     "לטבלת המטרות אין תאריך — נשמרה בתאריך המפגש הראשון (10/03/2026)"),
])
def test_undated_goals_table(tmp_path, start, expected_date, warning):
    parsed = _parse(_plan(tmp_path, start, [
        Table([["מטרות:"], ["", "מטרה כללית"], ["תחביר", "משפט מורכב"]]),  # goal with no domain
        "10/03/2026", "מפגש.",
    ]))

    assert parsed["goals_rows"] == [
        {"session_date": expected_date, "goals_text": "מטרה כללית\nתחביר: משפט מורכב"}]
    assert parsed["warnings"] == [warning]


def test_undated_goals_table_without_start_or_sessions_blocks(tmp_path):     # 2-ד
    with pytest.raises(ParseBlocked, match="לטבלת המטרות אין תאריך"):
        _parse(_plan(tmp_path, "", [Table([["מטרות בתאריך"], ["תחביר", "משפט"]])]))


def test_goals_table_with_only_empty_rows_is_no_record(tmp_path):            # 2-ה
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        Table([["מטרות בתאריך 01/03/2026"], ["", ""]]), "01/03/2026", "מפגש."]))
    assert parsed["goals_rows"] == []


@pytest.mark.parametrize("rows", [                                            # 2-א, 2-ב
    [["סיכום"], ["א", "ב"]],                          # not a goals table
    [["מטרות לשנה הבאה"], ["תחביר", "משפט"]],        # title text that is not a date
    [["מטרות:"], ["תחביר", "משפט", "בבית"]],          # 3 columns
    [["מטרות:"], ["תחביר", "משפט"], ["הערה כללית"]],  # merged row inside the table
    [["שורה", "רגילה"], ["תאריך:", "תחום טיפול:"], ["01/03/2026", "תחביר"]],  # non-merged row before header
    [["תאריך:", "תחום טיפול:"], ["הערה לפני הנתונים"], ["01/03/2026", "תחביר"]],  # note before data
    [["תאריך:", "תחום טיפול:"], ["בקרוב", "תחביר"]],                          # date cell not a date
])
def test_table_with_unknown_structure_blocks(tmp_path, rows):
    with pytest.raises(ParseBlocked, match="מבנה לא מוכר"):
        _parse(_plan(tmp_path, "01/03/2026", [Table(rows), "01/03/2026", "מפגש."]))


def test_impossible_title_date_blocks(tmp_path):                             # 24a-1
    with pytest.raises(DateError):
        _parse(_plan(tmp_path, "01/03/2026", [
            Table([["מטרות בתאריך 31/13/2026"], ["תחביר", "משפט"]])]))


_HOME_HEADER = ["תאריך:", "תחום טיפול:", "המטפל:", "מתי והיכן:", "יעדים:"]


def test_home_program_tables_one_record_per_date(tmp_path):        # 19, 3-א … 3-ה, 21b
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        Table([
            [_PATIENT],                                         # name row, skipped
            _HOME_HEADER,
            ["05/03/26", "שליפה", "אמא", "בבית", "מיון לקטגוריות\nתכונות"],
            ["", "אורל מוטור", "אבא", "בערב", ""],               # inherits; empty cell omitted
            ["בנוסף – לתאם פגישה עם הגננת."],                   # note row
        ]),
        "05/03/2026", "מפגש.",
        Table([
            ["תאריך:", "תחום טיפול:", "המטפל:", "יעדים:"],      # 4 columns, no name row
            ["12/03", "תחביר", "גננת", "משפט פשוט"],            # no year → previous date
            ["05/03/2026", "שטף", "אמא", "קריאה משותפת"],       # same date as table 1 → merged
        ]),
    ]))

    assert parsed["goals_rows"] == [
        {"session_date": "2026-03-05", "goals_text":
            "שליפה | המטפל: אמא | מתי והיכן: בבית | יעדים: מיון לקטגוריות; תכונות\n"
            "אורל מוטור | המטפל: אבא | מתי והיכן: בערב\n"
            "הערה: בנוסף – לתאם פגישה עם הגננת.\n"
            "שטף | המטפל: אמא | יעדים: קריאה משותפת"},
        {"session_date": "2026-03-12", "goals_text": "תחביר | המטפל: גננת | יעדים: משפט פשוט"},
    ]
    assert parsed["warnings"] == ["התאריך '12/03' נכתב בלי שנה — הונחה השנה 2026"]


def test_home_program_first_row_without_date_takes_the_start_date(tmp_path):   # 3-ג
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        Table([["תאריך:", "תחום טיפול:", "המטפל:", "יעדים:"], ["", "תחביר", "אמא", "משפט"]]),
        "05/03/2026", "מפגש.",
    ]))
    assert parsed["goals_rows"] == [
        {"session_date": "2026-03-01", "goals_text": "תחביר | המטפל: אמא | יעדים: משפט"}]
    assert parsed["warnings"] == [
        "לטבלת המטרות אין תאריך — נשמרה בתאריך תחילת הטיפול (01/03/2026)"]


def test_same_session_date_twice_is_one_record(tmp_path):                  # 21b, 3-ו
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        "08/03/2026", "שיחה עם אמא.", "08/03/2026", "שיחה עם המורה."]))
    assert parsed["session_blocks"] == [
        {"session_date": "2026-03-08", "session_text": "שיחה עם אמא.\nשיחה עם המורה."}]
    assert parsed["warnings"] == []


def test_unknown_table_message_names_the_table_by_number(tmp_path):        # 3-ח
    with pytest.raises(ParseBlocked) as exc:
        _parse(_plan(tmp_path, "01/03/2026", [
            Table([["מטרות:"], ["תחביר", "משפט"]]),
            Table([[_PATIENT], ["סיכום", "טקסט"]]),
        ]))
    assert "הטבלה ה-2" in str(exc.value)
    assert _PATIENT not in str(exc.value)


def test_goals_only_plan_has_no_sessions(tmp_path):                       # 22, 4-א
    parsed = _parse(_plan(tmp_path, "16/08/2026", [
        "מטרת על: ", Table([["מטרות בתאריך 16/8"], ["לקסיקון", "שליפה"]])]))

    assert parsed["session_blocks"] == []
    assert parsed["goals_rows"] == [{"session_date": "2026-08-16", "goals_text": "לקסיקון: שליפה"}]


@pytest.mark.parametrize("body", [                                         # 22, 4-ב
    [],                                            # header only
    ["מטרת על: שיפור השיח", "הערות: לעקוב"],      # header fields only
    ["טקסט בלי שום תאריך."],                       # free text without a date line
    ["01/03/2026"],                                # a date with no text
])
def test_plan_without_goals_or_sessions_blocks(tmp_path, body):
    with pytest.raises(ParseBlocked, match="לא נמצא תוכן בתוכנית הטיפול"):
        _parse(_plan(tmp_path, "01/03/2026", body))


def test_signature_block_is_not_stored(tmp_path):                          # N9, 4-ג
    parsed = _parse(_plan(tmp_path, "01/03/2026", [
        "05/03/2026", "מפגש.", "בברכה,", "המטפלת", "קלינאית תקשורת מ.ר. 00000",
        "12/03/2026", "מפגש שני.",
        "19/03/2026", "מפגש שלישי.", "המטפלת, מ.ר. 00000",
    ]))
    assert [b["session_text"] for b in parsed["session_blocks"]] == [
        "מפגש.", "מפגש שני.", "מפגש שלישי."]
