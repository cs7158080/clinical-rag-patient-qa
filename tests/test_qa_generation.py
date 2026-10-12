"""
test_qa_generation.py — QueryWorkflow.generate_step without a live API.

The LLM is replaced by a stub, so these tests make no network calls.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation.qa import QueryWorkflow, RetrievalResultEvent
from app.prompts.qa import ANSWER_TRUNCATED_WARNING
from app.storage.models import QueryParams, RetrievalResult


def _workflow() -> QueryWorkflow:
    config = SimpleNamespace(
        anthropic=SimpleNamespace(
            generation_model="claude-haiku-4-5-20251001",
            temperature_generation=0.2,
            max_tokens_generation=10000,
        ),
        anthropic_api_key="test-key",
    )
    return QueryWorkflow(config, db_path="", pinecone_index=None, reid_map_path="")


def _run_generate(wf: QueryWorkflow, stop_reason: str) -> str:
    wf._llm = MagicMock()
    wf._llm.acomplete = AsyncMock(
        return_value=SimpleNamespace(text="**שפה**\n\n- תשובה", raw={"stop_reason": stop_reason})
    )
    ctx = MagicMock()
    ctx.store.get = AsyncMock(return_value="שאלה")
    ev = RetrievalResultEvent(
        result=RetrievalResult(chunks=["chunk"], source_table="treatment_sessions", count=1),
        params=QueryParams(patient_id="p", intent="summarize"),
    )
    return asyncio.run(wf.generate_step(ctx, ev)).answer


def test_llm_uses_configured_max_tokens():
    assert _workflow()._llm.max_tokens == 10000


def test_truncated_answer_gets_visible_warning():
    answer = _run_generate(_workflow(), stop_reason="max_tokens")
    assert answer.startswith("**שפה**")
    assert answer.endswith(ANSWER_TRUNCATED_WARNING)


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
def test_complete_answer_has_no_warning(stop_reason):
    answer = _run_generate(_workflow(), stop_reason=stop_reason)
    assert ANSWER_TRUNCATED_WARNING not in answer
