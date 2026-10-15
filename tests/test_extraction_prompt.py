"""Tests for the extraction prompt (PLAN.md 25b)."""

from app.prompts.extraction import format_extraction_prompt


def test_prompt_maps_hebrew_document_names_to_types():
    prompt = format_extraction_prompt("2026-10-15")
    assert '- the question names "אבחון" → "diagnosis"' in prompt
    assert ('- the question names "סיכום טיפול", "בקשה להמשך טיפול", "סיכום ביניים" '
            '→ "clinic_visit_summary"') in prompt
    assert '→ "treatment_plan"' in prompt
    assert "|null" not in prompt.split("Intent definitions")[0]   # no null type any more
