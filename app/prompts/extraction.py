# prompt: extraction_v1
"""
extraction.py — Prompt templates for converting Hebrew NL queries into structured JSON parameters.

Temperature: 0 (set at call site in extractor.py).
Centralized per Prompt Engineering Guidelines — no prompt strings live in business logic.
"""

from app.ingestion.classify import TYPE_PHRASES
from app.query.router import FIXED_DOMAINS

EXTRACTION_SYSTEM = """You are a parameter extractor for queries from a speech-language pathologist.
Today: {today}.

Return JSON only, without explanation:
{{
  "date_from": "YYYY-MM-DD or null",
  "date_to": "YYYY-MM-DD or null",
  "session_count": null,
  "template_type": "diagnosis|clinic_visit_summary|treatment_plan",
  "topic": "minimal topic as a string or null",
  "intent": "summarize|find_specific|check_domain|compare_progress"
}}

Intent definitions:
- summarize: summary of a period/documents
- find_specific: searching for specific information
- check_domain: the question concerns one of the fixed clinical domains listed below
- compare_progress: comparing progress before/after

Document type (template_type) — decided only by the document the question names:
{doc_type_rules}
- anything else (sessions, meetings, guidance, goals, progress, or no document named)
  → "treatment_plan"
Minor variations still count as naming a document (definite article, prefix letters such as
ב/ה/ו/ל, e.g. "בסיכום הטיפול" names "סיכום טיפול"). Asking for a summary ("תסכמי…") is
intent="summarize", not a document name.

Fixed clinical domain taxonomy (closed list, exact Hebrew strings):
{domain_list}

If the question explicitly names one of the domains above (minor variations such as
a definite article, spelling, or inflection still count as naming it), set
intent="check_domain" and set topic to the EXACT string from the list above
(character-for-character, no added articles or punctuation).
Broad umbrella terms that are NOT themselves list items (e.g. "שפה", "תקשורת",
"דיבור") are NOT domains — do not map them to a list item; use intent="find_specific"
and copy the user's own word as the topic. Never invent, complete, or normalize a
topic the user did not say.
This rule applies only when no other intent fits better: wording about progress
before/after still means compare_progress, and a request for a full summary still
means summarize, even if a domain name is also mentioned.
For any other topic, return it as free text as before.

Relative dates → ISO 8601 (today: {today}).
session_count: integer when asking about recent sessions — 1 for "the last session", 3 for "last 3 sessions". null otherwise.
Return only valid JSON without markdown."""

EXTRACTION_RETRY_SYSTEM = """The previous output was not valid JSON. Try again.
Today: {today}. Return JSON only according to the schema:
{{"date_from": null, "date_to": null, "session_count": null, "template_type": null, "topic": null, "intent": "find_specific"}}"""


def _doc_type_rules() -> str:
    """One rule line per document type that has its own documents (PLAN.md 25b).

    Phrases come from the filename classifier — one source for both. Everything
    else is a treatment plan, which the prompt states separately.
    """
    lines = []
    for template_type in ("diagnosis", "clinic_visit_summary"):
        phrases = ", ".join(f'"{p}"' for p in TYPE_PHRASES[template_type])
        lines.append(f'- the question names {phrases} → "{template_type}"')
    return "\n".join(lines)


def format_extraction_prompt(today: str) -> str:
    """Return the extraction system prompt with today's date, document-type rules and domain list."""
    domain_list = "\n".join(f"- {d}" for d in sorted(FIXED_DOMAINS))
    return EXTRACTION_SYSTEM.format(
        today=today, domain_list=domain_list, doc_type_rules=_doc_type_rules())



def format_retry_prompt(today: str) -> str:
    """Return the retry system prompt with today's date injected."""
    return EXTRACTION_RETRY_SYSTEM.format(today=today)
