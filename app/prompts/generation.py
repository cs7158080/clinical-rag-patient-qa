# prompt: generation_v3
"""
generation.py — Prompt templates for generating treatment visit summaries.

Temperature: 0.2 (set at call site in generation/summary_generator.py).
The LLM receives the base document as JSON (4 sections + a domain-findings object)
and returns an updated JSON with the same keys. A parse failure raises
GenerationParseError — it must never silently produce an empty document.
"""

import json
import re

SECTION_KEYS = ['רקע', 'מהלך_האבחון', 'ממצאי_האבחון', 'סיכום_והמלצות']
DOMAINS_KEY = 'ממצאי_תחומים'


class GenerationParseError(ValueError):
    """Raised when the LLM output cannot be parsed into the expected JSON document."""


# ---------------------------------------------------------------------------
# System instruction block
# ---------------------------------------------------------------------------

GENERATE_SUMMARY_SYSTEM = """You are an assistant for a speech-language clinician. Update a treatment visit summary in Hebrew only.
Answer solely based on the information provided. Do not add information that does not appear in the sources.
Tokens such as PERSON_xxx and INST_xxx are names — use them naturally.
Write dates in Hebrew.
Write in plain, clear language understandable to parents and caregivers who are not speech-language clinicians.
When phonemic notation appears (e.g. /ר/, /ס/, /ל/) — write the sound name in words: 'הצליל ר', 'הצליל ס'.
Expand each described activity: state what was done, how the patient responded, and what was achieved."""

# ---------------------------------------------------------------------------
# Full generation prompt
# ---------------------------------------------------------------------------

GENERATE_SUMMARY_PROMPT = GENERATE_SUMMARY_SYSTEM + """

Base document to update (JSON — update only what the new information changes, leave unchanged values exactly as-is):
{base_document_json}

The key ממצאי_תחומים maps each clinical domain to its current findings text.

Treatment sessions ({date_from} to {date_to}):
{sessions_text}

Treatment goals for the period:
{goals_text}

Return JSON only — without additional text — with exactly the following keys:
רקע, מהלך_האבחון, ממצאי_האבחון, סיכום_והמלצות, ממצאי_תחומים
- The first four keys are strings. ממצאי_האבחון holds only the section's intro line.
- ממצאי_תחומים is an object with exactly the same domain keys as in the base document — do not add or remove domains; update a domain's text only when the treatment sessions justify it.
- Do not add new keys and do not remove existing keys.
- If there is no new information relevant to a value — return it exactly as it is in the base document."""

# ---------------------------------------------------------------------------
# Section parsing — JSON-based; failure raises, never silently falls back
# ---------------------------------------------------------------------------

def parse_generated_sections(llm_output: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse LLM JSON output into (sections, domains).

    Returns
    -------
    sections : dict mapping each SECTION_KEYS entry → text (stripped).
    domains  : dict mapping domain name → text (stripped).

    Raises
    ------
    GenerationParseError
        If the output is not valid JSON or ממצאי_תחומים is not an object.
    """
    text = llm_output.strip()

    for candidate in [text, _extract_json_block(text)]:
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        domains_raw = data.get(DOMAINS_KEY) or {}
        if not isinstance(domains_raw, dict):
            raise GenerationParseError(f"'{DOMAINS_KEY}' is not a JSON object")
        sections = {k: str(data.get(k, '')).strip() for k in SECTION_KEYS}
        domains = {str(k).strip(): str(v).strip() for k, v in domains_raw.items()}
        return sections, domains

    raise GenerationParseError("LLM output is not a valid JSON object")


def _extract_json_block(text: str) -> str | None:
    """Return the first {...} block found in text, or None."""
    match = re.search(r'\{[\s\S]*\}', text)
    return match.group(0) if match else None
