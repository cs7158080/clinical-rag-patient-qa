"""Tests for app/ingestion/dates.py — samples are the date forms found in the corpus."""

from datetime import date

import pytest

from app.ingestion.dates import DateError, parse_date, resolve_dates

TODAY = date(2026, 10, 11)


@pytest.mark.parametrize("text, expected", [
    # full year
    ("23/09/2024", (23, 9, 2024)),
    ("3/6/2026", (3, 6, 2026)),
    ("4/12/2025", (4, 12, 2025)),
    ("20/7/2021", (20, 7, 2021)),
    ("05.03.2026", (5, 3, 2026)),
    ("5.3.2026", (5, 3, 2026)),
    ("  08/02/2026  ", (8, 2, 2026)),
    # 2-digit year → 20YY (20a)
    ("1/1/24", (1, 1, 2024)),
    ("14/12/25", (14, 12, 2025)),
    ("01/12/25", (1, 12, 2025)),
    ("6/11/26", (6, 11, 2026)),
    ("4/1/26", (4, 1, 2026)),
    ("8/5/26", (8, 5, 2026)),
    ("10/7/26", (10, 7, 2026)),
    ("6/2/22", (6, 2, 2022)),
    ("05.03.26", (5, 3, 2026)),
    # no year (20b); trailing slash = no year
    ("20/05", (20, 5, None)),
    ("30/6", (30, 6, None)),
    ("7/9", (7, 9, None)),
    ("6/10", (6, 10, None)),
    ("20/05/", (20, 5, None)),
    ("29/2", (29, 2, None)),
])
def test_parse_date_valid(text, expected):
    p = parse_date(text)
    assert (p.day, p.month, p.year) == expected
    assert p.raw == text.strip()


@pytest.mark.parametrize("text", [
    "", "שלום",
    "מטרות בתאריך 8/5/26",        # date inside text — the caller extracts it
    "28/12.", "11/22.",           # date inside a sentence (corpus) — stays text
    "1.5", "1.5.",                # dot without a year = a number, not a date
    "1/2 כוס", "123/4/2024", "1/2/123", "1/2.2024",
])
def test_parse_date_not_a_date(text):
    assert parse_date(text) is None


@pytest.mark.parametrize("text", [
    "31/11/2025", "29/2/2025", "0/5/2026", "12/13/2026", "32/1",
])
def test_parse_date_impossible_raises_hebrew(text):
    with pytest.raises(DateError, match="תאריך לא תקין"):
        parse_date(text)


def _seq(*texts):
    return [parse_date(t) for t in texts]


def test_resolve_full_dates_no_warnings():
    r = resolve_dates(_seq("01/03/2026", "15/03/26", "2/4/2026"), start=date(2026, 3, 1), today=TODAY)
    assert r.dates == [date(2026, 3, 1), date(2026, 3, 15), date(2026, 4, 2)]
    assert r.warnings == []


def test_resolve_infers_year_from_previous_date():
    r = resolve_dates(_seq("08/05/2026", "20/06"), today=TODAY)
    assert r.dates[1] == date(2026, 6, 20)
    assert len(r.warnings) == 1
    assert "20/06" in r.warnings[0] and "2026" in r.warnings[0]


def test_resolve_infers_next_year_when_month_is_earlier():
    r = resolve_dates(_seq("01/12/2025", "05/01"), today=TODAY)
    assert r.dates[1] == date(2026, 1, 5)


def test_resolve_consecutive_partial_dates_across_year_end():
    r = resolve_dates(_seq("01/10/2025", "20/12", "05/01", "10/04"), today=TODAY)
    assert r.dates[1:] == [date(2025, 12, 20), date(2026, 1, 5), date(2026, 4, 10)]
    assert len(r.warnings) == 3


def test_resolve_first_partial_uses_start_date():
    r = resolve_dates(_seq("16/8"), start=date(2026, 8, 16), today=TODAY)   # מנחם
    assert r.dates == [date(2026, 8, 16)]
    r = resolve_dates(_seq("05/01"), start=date(2025, 11, 13), today=TODAY)
    assert r.dates == [date(2026, 1, 5)]


def test_resolve_previous_date_wins_over_start():               # חנה: start 2021
    r = resolve_dates(_seq("17/09/2026", "05/10"), start=date(2021, 7, 20), today=TODAY)
    assert r.dates[1] == date(2026, 10, 5)


def test_resolve_first_partial_uses_next_dated_item():          # יוסף: no start date
    r = resolve_dates(_seq("14/7", "22/07/2026"), today=TODAY)
    assert r.dates[0] == date(2026, 7, 14)
    assert len(r.warnings) == 1 and "14/7" in r.warnings[0]


def test_resolve_next_dated_item_previous_year_when_month_is_later():
    r = resolve_dates(_seq("20/12", "05/01/2026"), today=TODAY)
    assert r.dates[0] == date(2025, 12, 20)


def test_resolve_partial_without_reference_raises():
    with pytest.raises(DateError, match="בלי שנה"):
        resolve_dates(_seq("20/07"), today=TODAY)


def test_resolve_inferred_29_feb_in_non_leap_year_raises():
    with pytest.raises(DateError, match="תאריך לא תקין"):
        resolve_dates(_seq("01/01/2025", "29/2"), today=TODAY)


def test_out_of_order_is_not_warned():      # rule (1) removed 2026-10-12
    r = resolve_dates(_seq("23/09/2024", "30/10/24", "10/07/2024", "28/10/2024"), today=TODAY)
    assert r.warnings == []


def test_warning_future():                  # C4h חנוך
    r = resolve_dates(_seq("5/11/2026", "6/11/26"), start=date(2026, 11, 5), today=TODAY)
    assert len(r.warnings) == 2
    assert all("עתידי" in w for w in r.warnings)


def test_warning_before_start():            # C4f מלכה
    r = resolve_dates(_seq("8/6/22"), start=date(2026, 5, 31), today=TODAY)
    assert len(r.warnings) == 1
    assert "תחילת הטיפול" in r.warnings[0]


def test_today_is_not_future():
    assert resolve_dates(_seq("11/10/2026"), today=TODAY).warnings == []
