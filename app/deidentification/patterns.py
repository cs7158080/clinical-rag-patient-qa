"""
patterns.py — shared PII regex patterns.

Single source of truth for both Pass 1 (deid.py) and Pass 2 (validation.py),
so the two passes can never drift apart.
"""

# Israeli phone: contiguous, hyphen/space-segmented, and +972 forms.
# Digit-lookaround guards instead of \b (which fails around '+').
PHONE_REGEX = r"(?<!\d)(?:\+972[\s-]?|0)[1-9]\d?[\s-]?\d{3}[\s-]?\d{4}(?!\d)"

# National ID: 9 contiguous digits, hyphenated 8+1 (12345678-9), or bare
# 8 digits. Alternation order matters — longest form first.
NATIONAL_ID_REGEX = r"\b\d{9}\b|\b\d{8}-\d\b|\b\d{8}\b"

EMAIL_REGEX = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
