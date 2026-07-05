"""
extractor.py — LLM-based extraction: Hebrew NL query → structured QueryParams.

Uses Claude (extraction_model from config) with temperature=0.
Performs one application-level retry if the first response is not valid JSON.
If both attempts fail, returns None — the caller is responsible for returning
CANT_UNDERSTAND_MESSAGE to the user.
"""

import json
import logging
from datetime import date

from anthropic import Anthropic

from app.config import AppConfig
from app.prompts.extraction import format_extraction_prompt, format_retry_prompt
from app.storage.models import QueryParams

logger = logging.getLogger(__name__)


def extract_query_params(
    question: str,
    patient_id: str,
    config: AppConfig,
) -> QueryParams | None:
    """Extract structured query parameters from a Hebrew free-text question.

    Parameters
    ----------
    question:   The user's Hebrew free-text question.
    patient_id: The pre-resolved patient ID (sha256 of patient folder name).
                This is merged into the returned QueryParams — never sent to the LLM.
    config:     AppConfig instance providing API key and model settings.

    Returns
    -------
    A QueryParams dataclass on success, or None if extraction fails after two attempts.
    """
    today = date.today().isoformat()
    client = Anthropic(api_key=config.anthropic_api_key)

    logger.info("Extracting query params for question (len=%d)", len(question))

    for attempt in range(2):
        system = format_retry_prompt(today) if attempt == 1 else format_extraction_prompt(today)

        response = client.messages.create(
            model=config.anthropic.extraction_model,
            max_tokens=256,
            temperature=config.anthropic.temperature_extraction,
            system=system,
            messages=[{"role": "user", "content": question}],
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
            raw_count = data.get("session_count")
            params = QueryParams(
                patient_id=patient_id,
                date_from=data.get("date_from") or None,
                date_to=data.get("date_to") or None,
                template_type=data.get("template_type") or None,
                topic=data.get("topic") or None,
                intent=data.get("intent", "find_specific"),
                session_limit=int(raw_count) if raw_count else None,
            )
            logger.info(
                "Extracted params: intent=%s template=%s topic=%s date_from=%s date_to=%s session_limit=%s",
                params.intent,
                params.template_type,
                params.topic,
                params.date_from,
                params.date_to,
                params.session_limit,
            )
            return params
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning(
                "Extraction attempt %d produced invalid JSON. Raw output: %r",
                attempt + 1,
                raw[:200],
            )

    logger.error("Both extraction attempts failed — returning None")
    return None
