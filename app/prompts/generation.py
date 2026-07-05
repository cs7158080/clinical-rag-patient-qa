# prompt: generation_v2
"""
generation.py — Prompt templates for generating clinic visit summaries.

Temperature: 0.2 (set at call site in generation/summary_generator.py).
The generated document follows the Family A fixed-section structure.
LLM receives a base document as JSON and returns an updated JSON — no regex parsing needed.
"""

import json
import re

SECTION_KEYS = ['רקע', 'מהלך_האבחון', 'ממצאי_האבחון', 'סיכום_והמלצות', 'תמצית_אבחון']

# ---------------------------------------------------------------------------
# System instruction block
# ---------------------------------------------------------------------------

GENERATE_SUMMARY_SYSTEM = """You are an assistant for a speech-language clinician. Update a clinic visit summary in Hebrew only.
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

Base document to update (JSON — update only what the new information changes, leave unchanged sections as-is):
{base_document_json}

Treatment sessions ({date_from} to {date_to}):
{sessions_text}

Treatment goals for the period:
{goals_text}

Return JSON only — without additional text — with exactly the following keys:
רקע, מהלך_האבחון, ממצאי_האבחון, סיכום_והמלצות, תמצית_אבחון

Do not add new keys and do not remove existing keys.
If there is no new information relevant to a section — leave it as it is in the base document."""

# ---------------------------------------------------------------------------
# Section parsing — JSON-based, regex fallback
# ---------------------------------------------------------------------------

def parse_generated_sections(llm_output: str) -> dict[str, str]:
    """Parse LLM JSON output into a section dict.

    Attempts json.loads() on the raw output (or the first JSON object found).
    Falls back to empty strings for all sections on any parse failure.

    Returns
    -------
    dict mapping section key → text (stripped). Always contains all SECTION_KEYS.
    """
    empty = {k: '' for k in SECTION_KEYS}

    text = llm_output.strip()

    # Try direct parse first, then extract first {...} block
    for candidate in [text, _extract_json_block(text)]:
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return {k: str(data.get(k, '')).strip() for k in SECTION_KEYS}
        except (json.JSONDecodeError, ValueError):
            continue

    return empty


def _extract_json_block(text: str) -> str | None:
    """Return the first {...} block found in text, or None."""
    match = re.search(r'\{[\s\S]*\}', text)
    return match.group(0) if match else None
