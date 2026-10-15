"""Tests for the treatment-plan parser (PLAN.md items 15–24a, section 7 T2)."""

import pytest

from app.ingestion.adapter_b import parse_treatment_plan
from app.ingestion.dates import DateError
from app.ingestion.result import ParseBlocked
from tests.fixtures.docx_factory import write_docx

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
