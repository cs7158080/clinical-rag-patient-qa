"""
judge.py — LLM-as-judge for evaluation of clinical Q&A answers.

Uses Claude to score each answer on four criteria (1-5 scale each):
    faithfulness   — answer is grounded in retrieved data only (no hallucination)
    completeness   — answer addresses the question fully
    date_accuracy  — all dates mentioned are correct
    hebrew_fluency — answer is clear, natural Hebrew

# prompt: judge_v1
"""

import json
import logging

from anthropic import Anthropic

from app.config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """# prompt: judge_v1
אתה שופט שמדרג תשובות של מערכת Q&A קלינית.
דרג את התשובה בסולם 1-5 לפי 4 קריטריונים.
החזר JSON בלבד:
{{"faithfulness": 1-5, "completeness": 1-5, "date_accuracy": 1-5, "hebrew_fluency": 1-5}}

הגדרות:
- faithfulness: האם התשובה מתבססת רק על הנתונים, ללא המצאה
- completeness: האם התשובה עונה על השאלה במלואה
- date_accuracy: האם התאריכים בתשובה מדויקים
- hebrew_fluency: האם העברית תקינה וטבעית

שאלה: {question}
תשובה צפויה: {expected}
תשובה בפועל: {actual}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def judge_answer(
    question: str,
    actual_answer: str,
    expected_answer: str,
    config: AppConfig,
) -> dict:
    """Score *actual_answer* against *expected_answer* on 4 criteria.

    Returns a dict with keys: faithfulness, completeness, date_accuracy,
    hebrew_fluency (each an int 1-5).  Returns all-zeros on error.

    Parameters
    ----------
    question:        The original Hebrew question.
    actual_answer:   The system's answer to evaluate.
    expected_answer: The manually written ground-truth answer.
    config:          AppConfig — used for API key and model name.
    """
    client = Anthropic(api_key=config.anthropic_api_key)
    prompt = JUDGE_PROMPT.format(
        question=question,
        expected=expected_answer,
        actual=actual_answer,
    )

    try:
        response = client.messages.create(
            model=config.anthropic.extraction_model,
            max_tokens=128,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()
        logger.info(f"Getting response:{raw_text}")
        scores = json.loads(raw_text)
        return scores
    except json.JSONDecodeError as exc:
        logger.error("judge_answer: JSON parse error — %s", exc)
        return {"faithfulness": 0, "completeness": 0, "date_accuracy": 0, "hebrew_fluency": 0}
    except Exception as exc:  # noqa: BLE001
        logger.error("judge_answer: unexpected error — %s", exc)
        return {"faithfulness": 0, "completeness": 0, "date_accuracy": 0, "hebrew_fluency": 0}
