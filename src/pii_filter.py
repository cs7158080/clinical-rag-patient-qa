# src/pii_filter.py
import re

PATTERNS = [
    # --- Phone numbers (must come before 9-digit ID) ---

    # Parentheses: (052) 0000000 | (052) 000-0000
    (r"\(0(?:5[0-9]|[2-489]|7[2-9])\)[-.\s]?\d{3}[-.\s]?\d{4}", "[טלפון מוסתר]"),

    # Local: 0520000000 | 052-0000000 | 052-000-0000 | 052 000 0000 | 052.000.0000
    # Covers mobile (05X), landline (02/03/04/08/09), VoIP (07X)
    (r"\b0(?:5[0-9]|[2-489]|7[2-9])[-.\s]?\d{3}[-.\s]?\d{4}\b", "[טלפון מוסתר]"),

    # --- Israeli ID (ת.ז.) ---
    # 9-digit number not surrounded by other digits
    (r"\b\d{9}\b", "[ת.ז. מוסתרת]"),

    # --- Email ---
    (r"[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,}", "[מייל מוסתר]")
]


def anonymize_text(text: str) -> str:
    for pattern, placeholder in PATTERNS:
        text = re.sub(pattern, placeholder, text)
    return text
