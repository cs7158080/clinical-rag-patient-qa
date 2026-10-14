"""Tests for the Family A parser (PLAN.md items 8–14b)."""

import asyncio
from types import SimpleNamespace

import pytest

from app.ingestion import pipeline
from app.ingestion.adapter_a import parse_family_a
from app.ingestion.dates import DateError
from app.ingestion.result import ParseBlocked
from app.prompts.extraction import format_extraction_prompt
from app.query.router import FIXED_DOMAINS
from tests.fixtures.docx_factory import write_docx

_PATIENT = "ישראל ישראלי"


def _family_a(tmp_path, top: list[str]) -> str:
    """A minimal diagnosis whose header (after בס"ד) is *top*."""
    return str(write_docx(tmp_path / f"סיכום אבחון {_PATIENT}.docx",
                          ['בס"ד', *top, f"שם: {_PATIENT}", "רקע:", "ילד בן שש."]))


def _parse(path):
    return parse_family_a(path, _PATIENT, "diagnosis")


def test_shift_enter_lines_are_parsed_as_paragraphs(tmp_path):
    # item 11: heading + content, and a whole findings section, in one paragraph each
    path = write_docx(tmp_path / "סיכום אבחון ישראל ישראלי.docx", [
        'בס"ד',
        "01/03/2026",
        "שם: ישראל ישראלי\nת.ל.: 01/01/2020\nת.ז.: 000000000",
        "רקע:\nילד בן שש, דובר עברית.",
        "ממצאי האבחון:\nלהלן תיאור יכולותיו:\nהבנת שפה – הבנה תקינה.\nתחביר –\nמשפטים פשוטים.",
    ])

    parsed = parse_family_a(str(path), "ישראל ישראלי", "diagnosis")

    assert parsed["header"]["name"] == "ישראל ישראלי"
    assert parsed["header"]["date_of_birth"] == "01/01/2020"
    assert parsed["sections"]["רקע"] == "ילד בן שש, דובר עברית."
    assert parsed["sections"]["ממצאי_האבחון"] == "להלן תיאור יכולותיו:"
    assert parsed["domains"] == {"הבנת שפה": "הבנה תקינה.", "תחביר": "משפטים פשוטים."}


@pytest.mark.parametrize("top, expected", [
    (["01/03/2026", "לכבוד", "הנדון: סיכום אבחון"], "2026-03-01"),   # item 8: bare date line
    (["תאריך: 1/3/26", "הנדון: סיכום אבחון"], "2026-03-01"),         # 'תאריך:' + 2-digit year
    (["לכבוד", "01/03/2026"], "2026-03-01"),                          # 3-א: no הנדון → up to רקע
])
def test_document_date(tmp_path, top, expected):
    parsed = _parse(_family_a(tmp_path, top))
    assert parsed["header"]["date"] == expected
    assert parsed["warnings"] == []


def test_date_after_subject_line_is_not_the_document_date(tmp_path):    # 3-א, 8b
    with pytest.raises(ParseBlocked, match="לא נמצא תאריך מסמך"):
        _parse(_family_a(tmp_path, ["הנדון: סיכום אבחון", "01/03/2026"]))


def test_day_past_month_end_is_last_day_with_warning(tmp_path):        # item 10
    parsed = _parse(_family_a(tmp_path, ["31/11/2025", "הנדון: סיכום אבחון"]))
    assert parsed["header"]["date"] == "2025-11-30"
    assert parsed["warnings"] == ["התאריך '31/11/2025' לא קיים — נשמר כ-30/11/2025, כדאי לבדוק"]


def test_future_document_date_is_stored_with_warning(tmp_path):        # 3-ב
    parsed = _parse(_family_a(tmp_path, ["01/01/2099", "הנדון: סיכום אבחון"]))
    assert parsed["header"]["date"] == "2099-01-01"
    assert parsed["warnings"] == ["התאריך '01/01/2099' עתידי — כדאי לבדוק"]


@pytest.mark.parametrize("written", ["13/13/2025", "20/05"])           # 3-ג
def test_impossible_or_yearless_document_date_blocks(tmp_path, written):
    with pytest.raises(DateError):
        _parse(_family_a(tmp_path, [written, "הנדון: סיכום אבחון"]))


@pytest.mark.parametrize("line, expected", [                           # item 9, 3-ה
    ("ת.ל.: 08/08/2008", "08/08/2008"),
    ("ת.ל: 08/08/2008", "08/08/2008"),
    ("ת.ל. : 08/08/2008", "08/08/2008"),
    ("ת.ל :08/08/2008", "08/08/2008"),
    ("ת.ל. 08/08/2008  ת.ז.: 000", None),       # no colon after ת.ל — never takes the ID
])
def test_date_of_birth_label_variants(tmp_path, line, expected):
    parsed = _parse(_family_a(tmp_path, ["01/03/2026", line]))
    assert parsed["header"]["date_of_birth"] == expected


def test_missing_date_is_reported_as_blocked(tmp_path, monkeypatch):    # 3-ד, W2
    patient = tmp_path / _PATIENT
    patient.mkdir()
    write_docx(patient / f"סיכום אבחון {_PATIENT}.docx",
               ['בס"ד', "הנדון: סיכום אבחון", "רקע:", "ילד בן שש."])
    monkeypatch.setattr(pipeline, "load_ner_model", lambda models_dir: None)
    config = SimpleNamespace(models_dir="", run_mode="test", data_dir=str(tmp_path))

    [(_, result)] = asyncio.run(pipeline.run_ingestion(
        [str(tmp_path)], config, {}, None, None, dry_run=True))

    assert result.status == "blocked"
    assert result.message.startswith("לא נמצא תאריך מסמך")


def test_domains_under_course_section(tmp_path):                       # item 13
    path = write_docx(tmp_path / f"סיכום טיפול {_PATIENT}.docx", [
        'בס"ד', "01/03/2026", "הנדון: סיכום טיפול",
        "רקע:", "ילד בן שש.",
        "מהלך הטיפול:", "להלן תפקודו בתחומים בהם עסק הטיפול:",
        "הבנת שפה – הבנה תקינה.", "שטף - תקין",
        "סיכום והמלצות:", "שטף טוב, מומלץ המשך מעקב.",
    ])

    parsed = parse_family_a(str(path), _PATIENT, "clinic_visit_summary")

    assert parsed["sections"]["מהלך_האבחון"] == "להלן תפקודו בתחומים בהם עסק הטיפול:"
    assert parsed["domains"] == {"הבנת שפה": "הבנה תקינה.", "שטף": "תקין"}
    assert parsed["sections"]["סיכום_והמלצות"] == "שטף טוב, מומלץ המשך מעקב."


def test_domain_synonyms_and_play(tmp_path):                           # item 14, 5-א, 5-ג
    path = write_docx(tmp_path / f"סיכום אבחון {_PATIENT}.docx", [
        'בס"ד', "01/03/2026", "ממצאי האבחון:",
        "שטף, קול - תקין",
        "משחק\nנצפה משחק סימבולי קצר.",
        "אוצר מילים – אוצר מילים דל.",
        "לקסיקון ושליפה:\nקשיי שליפה.",
        "מובנות דיבור ויכולות אורומוטוריות:\nמובנות טובה.",
    ])

    assert _parse(str(path))["domains"] == {
        "שטף": "תקין",
        "משחק": "נצפה משחק סימבולי קצר.",
        "לקסיקון": "אוצר מילים דל.\nקשיי שליפה.",      # two synonyms, one domain
        "מובנות הדיבור": "מובנות טובה.",
    }


def test_domain_name_must_be_a_whole_word(tmp_path):                   # 5-ב
    path = write_docx(tmp_path / f"סיכום אבחון {_PATIENT}.docx", [
        'בס"ד', "01/03/2026", "ממצאי האבחון:",
        "תחביר – משפטים פשוטים.", "תחבירית נשמעו שיבושים.",
    ])

    assert _parse(str(path))["domains"] == {"תחביר": "משפטים פשוטים.\nתחבירית נשמעו שיבושים."}


def test_play_reaches_chat_routing_and_extraction_prompt():            # 5-ג, 5-ד
    assert "משחק" in FIXED_DOMAINS and len(FIXED_DOMAINS) == 14
    assert "- משחק" in format_extraction_prompt("2026-10-14")
