"""
classify.py — document type from the filename only (PLAN.md items 1–5, 3-X).

A type phrase may appear anywhere in the name. Phrases are whole and never
overlap across types, so the order of checks does not matter. The caller
decides what to do with zero matches (ignored) or several types (blocked).
"""

import os
import re

# Template type -> phrases that identify it (T1 decisions 1-א/ב/ג).
TYPE_PHRASES: dict[str, tuple[str, ...]] = {
    "diagnosis": ("אבחון",),
    "clinic_visit_summary": ("סיכום טיפול", "בקשה להמשך טיפול", "סיכום ביניים"),
    "treatment_plan": ("תוכנית טיפול", "תכנית טיפול"),
}

# Hebrew names of the types, for UI messages.
TYPE_LABELS: dict[str, str] = {
    "diagnosis": "אבחון",
    "clinic_visit_summary": "סיכום טיפול",
    "treatment_plan": "תוכנית טיפול",
}


def classify_filename(filename: str) -> list[str]:
    """Return every template type whose phrase appears in *filename*.

    The extension is dropped and runs of whitespace are collapsed first, so
    'סיכום  אבחון.docx' still matches. Types are returned in TYPE_PHRASES
    order; a type matched by two phrases is listed once.
    """
    name = re.sub(r"\s+", " ", os.path.splitext(filename)[0]).strip()
    return [
        template_type
        for template_type, phrases in TYPE_PHRASES.items()
        if any(phrase in name for phrase in phrases)
    ]
