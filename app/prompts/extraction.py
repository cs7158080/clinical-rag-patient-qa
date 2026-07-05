# prompt: extraction_v1
"""
extraction.py — Prompt templates for converting Hebrew NL queries into structured JSON parameters.

Temperature: 0 (set at call site in extractor.py).
Centralized per Prompt Engineering Guidelines — no prompt strings live in business logic.
"""

EXTRACTION_SYSTEM = """You are a parameter extractor for queries from a speech-language pathologist.
Today: {today}.

Return JSON only, without explanation:
{{
  "date_from": "YYYY-MM-DD or null",
  "date_to": "YYYY-MM-DD or null",
  "session_count": null,
  "template_type": "diagnosis|clinic_visit_summary|treatment_plan|null",
  "topic": "minimal topic as a string or null",
  "intent": "summarize|find_specific|check_domain|compare_progress"
}}

Intent definitions:
- summarize: summary of a period/documents
- find_specific: searching for specific information
- check_domain: checking a specific clinical domain (auditory memory, pragmatics, etc.)
- compare_progress: comparing progress before/after

Relative dates → ISO 8601 (today: {today}).
session_count: integer when asking about recent sessions — 1 for "the last session", 3 for "last 3 sessions". null otherwise.
Return only valid JSON without markdown."""

EXTRACTION_RETRY_SYSTEM = """The previous output was not valid JSON. Try again.
Today: {today}. Return JSON only according to the schema:
{{"date_from": null, "date_to": null, "session_count": null, "template_type": null, "topic": null, "intent": "find_specific"}}"""


def format_extraction_prompt(today: str) -> str:
    """Return the extraction system prompt with today's date injected."""
    return EXTRACTION_SYSTEM.format(today=today)


def format_retry_prompt(today: str) -> str:
    """Return the retry system prompt with today's date injected."""
    return EXTRACTION_RETRY_SYSTEM.format(today=today)
