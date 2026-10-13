"""Tests for the Family A parser (PLAN.md items 8–14b)."""

from app.ingestion.adapter_a import parse_family_a
from tests.fixtures.docx_factory import write_docx


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
