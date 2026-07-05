# LlamaIndex Query Workflow.
from llama_index.core.workflow import Workflow, StartEvent, StopEvent, step, Context, Event
from llama_index.llms.anthropic import Anthropic as LlamaAnthropic
from typing import Union
import logging

from app.query.extractor import extract_query_params
from app.query.router import route
from app.query.retrieval import retrieve
from app.storage.models import QueryParams, RetrievalResult
from app.deidentification.reid_map import reidentify_text, load as load_reid_map
from app.prompts.qa import (
    SUMMARIZE_PROMPT, FIND_SPECIFIC_PROMPT, CHECK_DOMAIN_PROMPT,
    COMPARE_PROGRESS_PROMPT, FAMILY_A_PROMPT,
    NO_RESULTS_MESSAGE, ERROR_MESSAGE, CANT_UNDERSTAND_MESSAGE,
    REID_MAP_MISSING_WARNING
)
from app.config import AppConfig

logger = logging.getLogger(__name__)


class QueryStartEvent(StartEvent):
    question: str
    patient_id: str


class QueryParamsEvent(Event):
    params: QueryParams


class RouteDecisionEvent(Event):
    decision: object  # RouteDecision


class RetrievalResultEvent(Event):
    result: RetrievalResult
    params: QueryParams


class EmptyResultEvent(Event):
    message: str


class TokenizedAnswerEvent(Event):
    answer: str


class QueryWorkflow(Workflow):
    def __init__(self, config: AppConfig, db_path: str, pinecone_index, reid_map_path: str, **kwargs):
        super().__init__(**kwargs)
        self._config = config
        self._db_path = db_path
        self._pinecone_index = pinecone_index
        self._reid_map_path = reid_map_path
        self._llm = LlamaAnthropic(
            model=config.anthropic.generation_model,
            api_key=config.anthropic_api_key,
            temperature=config.anthropic.temperature_generation,
        )

    @step
    async def extract_step(self, ctx: Context, ev: QueryStartEvent) -> Union[QueryParamsEvent, StopEvent]:
        params = extract_query_params(ev.question, ev.patient_id, self._config)
        if params is None:
            return StopEvent(result=CANT_UNDERSTAND_MESSAGE)
        await ctx.store.set("question", ev.question)
        return QueryParamsEvent(params=params)

    @step
    async def route_step(self, ctx: Context, ev: QueryParamsEvent) -> RouteDecisionEvent:
        decision = route(ev.params)
        return RouteDecisionEvent(decision=decision)

    @step
    async def retrieve_step(self, ctx: Context, ev: RouteDecisionEvent) -> Union[RetrievalResultEvent, EmptyResultEvent]:
        result = retrieve(ev.decision, self._config, self._db_path, self._pinecone_index)
        if isinstance(result, str):
            return EmptyResultEvent(message=result)
        if result.count == 0:
            return EmptyResultEvent(message=NO_RESULTS_MESSAGE)
        return RetrievalResultEvent(result=result, params=ev.decision.params)

    @step
    async def empty_step(self, ctx: Context, ev: EmptyResultEvent) -> StopEvent:
        return StopEvent(result=ev.message)

    @step
    async def generate_step(self, ctx: Context, ev: RetrievalResultEvent) -> TokenizedAnswerEvent:
        question = await ctx.store.get("question", default="")
        result = ev.result
        params = ev.params

        try:
            if result.source_table == 'treatment_sessions' and isinstance(result.chunks, dict):
                before_text = '\n---\n'.join(result.chunks.get('before', []))
                after_text = '\n---\n'.join(result.chunks.get('after', []))
                prompt = COMPARE_PROGRESS_PROMPT.format(
                    date_ref=params.date_from or '',
                    context_before=before_text,
                    context_after=after_text,
                    question=question,
                )
            elif params.intent == 'check_domain':
                context = '\n---\n'.join(result.chunks)
                prompt = CHECK_DOMAIN_PROMPT.format(domain=params.topic or '', context=context, question=question)
            elif result.source_table == 'family_a_sections':
                context = '\n---\n'.join(result.chunks)
                prompt = FAMILY_A_PROMPT.format(context=context, question=question)
            else:
                context = '\n---\n'.join(result.chunks if isinstance(result.chunks, list) else [])
                prompt = (
                    SUMMARIZE_PROMPT.format(context=context, question=question)
                    if params.intent == 'summarize'
                    else FIND_SPECIFIC_PROMPT.format(context=context, question=question)
                )

            logger.info(f"Calling LLM for intent={params.intent}")
            response = await self._llm.acomplete(prompt)
            answer = response.text.strip()
            if not answer:
                return TokenizedAnswerEvent(answer=ERROR_MESSAGE)
            return TokenizedAnswerEvent(answer=answer)
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return TokenizedAnswerEvent(answer=ERROR_MESSAGE)

    @step
    async def reidentify_step(self, ctx: Context, ev: TokenizedAnswerEvent) -> StopEvent:
        try:
            reid_map = load_reid_map(self._reid_map_path)
        except Exception:
            logger.warning("Re-id map missing during Q&A")
            final = ev.answer + '\n\n' + REID_MAP_MISSING_WARNING
            return StopEvent(result=final)

        final = reidentify_text(reid_map, ev.answer)
        logger.info("Answer generated and re-identified")
        return StopEvent(result=final)


async def run_query(
    question: str,
    patient_id: str,
    config: AppConfig,
    db_path: str,
    pinecone_index,
    reid_map_path: str,
) -> str:
    workflow = QueryWorkflow(
        config=config,
        db_path=db_path,
        pinecone_index=pinecone_index,
        reid_map_path=reid_map_path,
        timeout=120,
    )
    result = await workflow.run(question=question, patient_id=patient_id)
    return result
