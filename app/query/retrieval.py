"""
retrieval.py — Retrieval layer: SQLite + Pinecone fetch functions.

Accepts a RouteDecision and returns either a RetrievalResult (on success)
or a str (a user-facing Hebrew message when no data is found or an edge
case is hit before the LLM is called).

The caller (generation/qa.py RetrieveStep) must handle both return types:
- RetrievalResult → proceed to LLM generation
- str             → return directly to the user (no LLM call)
"""

import logging

from app.config import AppConfig
from app.prompts.qa import (
    NO_RESULTS_MESSAGE,
    NO_SESSIONS_AFTER,
    NO_SESSIONS_BEFORE,
)
from app.query.router import RouteDecision
from app.storage import db, pinecone_client
from app.storage.models import QueryParams, RetrievalResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def retrieve(
    decision: RouteDecision,
    config: AppConfig,
    db_path: str,
    pinecone_index,
) -> RetrievalResult | str:
    """Dispatch retrieval based on *decision.strategy*.

    Parameters
    ----------
    decision:       RouteDecision produced by router.route().
    config:         AppConfig (needed for Cohere API key and model name).
    db_path:        Absolute path to the SQLite database file.
    pinecone_index: An initialised Pinecone Index object, or None if unavailable.

    Returns
    -------
    RetrievalResult on success, or a Hebrew user-facing str when no results
    are found or an edge case short-circuits before the LLM.
    """
    params = decision.params
    strategy = decision.strategy

    # Error decision — return the pre-set message immediately
    if strategy == 'error':
        return decision.error_message  # type: ignore[return-value]

    # -- family_a_sqlite -----------------------------------------------------
    if strategy == 'family_a_sqlite':
        return _fetch_family_a(db_path, params)

    # -- treatment_sessions_sqlite -------------------------------------------
    if strategy == 'treatment_sessions_sqlite':
        return _fetch_treatment_sessions(db_path, params)

    # -- domain_sqlite -------------------------------------------------------
    if strategy == 'domain_sqlite':
        return _fetch_domain(db_path, params, config, pinecone_index)

    # -- compare_sqlite ------------------------------------------------------
    if strategy == 'compare_sqlite':
        return _fetch_compare(db_path, params)

    # -- pinecone ------------------------------------------------------------
    if strategy == 'pinecone':
        return _pinecone_retrieve(params, config, db_path, pinecone_index)

    # Unknown strategy — defensive fallback
    logger.error("Unknown retrieval strategy: %s", strategy)
    return NO_RESULTS_MESSAGE


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _fetch_family_a(db_path: str, params: QueryParams) -> RetrievalResult | str:
    """Fetch from family_a_sections, applying date resolution per Step 6."""
    # date resolution: date_latest=true OR (date_from is None and date_latest=False)
    # → treat as date_latest=True → fetch latest single row
    if params.session_limit is not None or params.date_from is None:
        chunk = db.fetch_latest_family_a(db_path, params.patient_id, params.template_type)
        chunks = [chunk] if chunk else []
    else:
        chunks = db.fetch_family_a_sections(
            db_path,
            params.patient_id,
            params.template_type,
            params.date_from,
            params.date_to,
        )

    texts = [c.text_deidentified for c in chunks if c.text_deidentified]
    if not texts:
        return NO_RESULTS_MESSAGE

    logger.info(
        "family_a_sqlite: retrieved %d chunk(s) for patient=%s template=%s",
        len(texts),
        params.patient_id,
        params.template_type,
    )
    return RetrievalResult(chunks=texts, source_table='family_a_sections', count=len(texts))


def _fetch_treatment_sessions(db_path: str, params: QueryParams) -> RetrievalResult | str:
    """Fetch from treatment_sessions with optional date filter or session limit."""
    if params.session_limit is not None:
        sessions = db.fetch_latest_n_treatment_sessions(
            db_path, params.patient_id, params.session_limit
        )
    else:
        sessions = db.fetch_treatment_sessions(
            db_path, params.patient_id, params.date_from, params.date_to
        )
    texts = [s.session_text_deidentified for s in sessions if s.session_text_deidentified]
    if not texts:
        return NO_RESULTS_MESSAGE

    logger.info(
        "treatment_sessions_sqlite: retrieved %d session(s) for patient=%s",
        len(texts),
        params.patient_id,
    )
    return RetrievalResult(chunks=texts, source_table='treatment_sessions', count=len(texts))


def _fetch_domain(
    db_path: str,
    params: QueryParams,
    config: AppConfig,
    pinecone_index,
) -> RetrievalResult | str:
    """Fetch from domain_findings; handle parent domains; fall back to Pinecone if no rows found."""
    # Try exact domain match first
    findings = db.fetch_domain_finding(db_path, params.patient_id, params.topic)

    # If no exact match, check if it's a parent domain (like "הבעת שפה")
    if not findings:
        findings = db.fetch_domain_by_parent(db_path, params.patient_id, params.topic)

    if findings:
        texts = [f.domain_text_deidentified for f in findings if f.domain_text_deidentified]
        if texts:
            logger.info(
                "domain_sqlite: retrieved %d finding(s) for patient=%s domain=%s (exact + parent)",
                len(texts),
                params.patient_id,
                params.topic,
            )
            return RetrievalResult(chunks=texts, source_table='domain_findings', count=len(texts))

    # No domain rows (exact or parent) — fall back to Pinecone semantic search
    logger.info(
        "domain_sqlite: no findings for domain=%s — falling back to Pinecone", params.topic
    )
    return _pinecone_retrieve(params, config, db_path, pinecone_index)


def _fetch_compare(db_path: str, params: QueryParams) -> RetrievalResult | str:
    """Fetch sessions before/after date_from for compare_progress intent.

    Edge cases (from Step 6):
    - date_from is None → cannot determine reference date → return NO_RESULTS_MESSAGE
    - No sessions before → NO_SESSIONS_BEFORE
    - No sessions after  → NO_SESSIONS_AFTER
    - No sessions on either side → NO_RESULTS_MESSAGE
    LLM is invoked only when both sides are non-empty.
    """
    if params.date_from is None:
        logger.warning("compare_sqlite: date_from is None — cannot compare without reference date")
        return NO_RESULTS_MESSAGE

    before = db.fetch_treatment_sessions_before(db_path, params.patient_id, params.date_from)
    after = db.fetch_treatment_sessions_after(db_path, params.patient_id, params.date_from)

    if not before and not after:
        return NO_RESULTS_MESSAGE
    if not before:
        return NO_SESSIONS_BEFORE
    if not after:
        return NO_SESSIONS_AFTER

    before_texts = [s.session_text_deidentified for s in before if s.session_text_deidentified]
    after_texts = [s.session_text_deidentified for s in after if s.session_text_deidentified]

    logger.info(
        "compare_sqlite: %d before / %d after sessions for patient=%s date_ref=%s",
        len(before_texts),
        len(after_texts),
        params.patient_id,
        params.date_from,
    )

    # chunks is a dict to allow the GenerateStep to build the comparison prompt
    return RetrievalResult(
        chunks={'before': before_texts, 'after': after_texts},
        source_table='treatment_sessions',
        count=len(before_texts) + len(after_texts),
    )


# ---------------------------------------------------------------------------
# Pinecone retrieval (shared helper)
# ---------------------------------------------------------------------------

def _pinecone_retrieve(
    params: QueryParams,
    config: AppConfig,
    db_path: str,
    pinecone_index,
) -> RetrievalResult | str:
    """Perform semantic search via Pinecone.

    Falls back to SQLite fetch-all when Pinecone is unavailable or raises an
    exception. Returns NO_RESULTS_MESSAGE if the query succeeds but returns
    zero matches (no SQLite fallback in the zero-results case per Step 6).

    Parameters
    ----------
    params:         QueryParams (patient_id, topic, date filters).
    config:         AppConfig for Cohere credentials and model name.
    db_path:        Path to SQLite DB (needed for the exception fallback).
    pinecone_index: Initialised Pinecone Index, or None if unavailable.
    """
    if pinecone_index is None:
        logger.warning(
            "Pinecone index is None — falling back to SQLite fetch-all for patient=%s",
            params.patient_id,
        )
        return _sqlite_fallback(db_path, params)

    date_from = params.date_from
    date_to = params.date_to
    if params.session_limit is not None:
        all_dates = db.get_treatment_session_dates(db_path, params.patient_id)
        recent = all_dates[: params.session_limit]
        if recent:
            date_from = min(recent)
            date_to = max(recent)
            logger.info(
                "session_limit=%d resolved to date_from=%s date_to=%s for patient=%s",
                params.session_limit,
                date_from,
                date_to,
                params.patient_id,
            )

    query_text = params.topic or ''
    try:
        query_vec = pinecone_client.get_cohere_query_embedding(
            query_text, config.cohere_api_key, config.cohere.model
        )
        matches = pinecone_client.query_pinecone(
            pinecone_index,
            query_vec,
            params.patient_id,
            top_k=10,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as exc:
        logger.warning(
            "Pinecone query failed (%s) — falling back to SQLite fetch-all for patient=%s",
            exc,
            params.patient_id,
        )
        return _sqlite_fallback(db_path, params)

    if not matches:
        logger.info(
            "Pinecone returned 0 matches for patient=%s topic=%s",
            params.patient_id,
            query_text,
        )
        return NO_RESULTS_MESSAGE

    # Pinecone only stores (patient_id, session_date, chunk_type) — fetch the actual text from SQLite
    texts = []
    for m in matches:
        meta = m.get('metadata', {})
        pid = meta.get('patient_id', '')
        sdate = meta.get('session_date', '')
        chunk_type = meta.get('chunk_type', '')

        if chunk_type == 'session_summary':
            rows = db.fetch_treatment_sessions(db_path, pid, sdate, sdate)
            if rows:
                texts.append(rows[0].session_text_deidentified)
        elif chunk_type == 'goals_row':
            goal = db.fetch_latest_treatment_goals(db_path, pid, sdate)
            if goal:
                texts.append(goal.goals_text_deidentified)

    if not texts:
        return NO_RESULTS_MESSAGE

    logger.info(
        "Pinecone returned %d match(es), resolved %d text(s) from SQLite for patient=%s",
        len(matches),
        len(texts),
        params.patient_id,
    )
    return RetrievalResult(chunks=texts, source_table='pinecone', count=len(texts))


def _sqlite_fallback(db_path: str, params: QueryParams) -> RetrievalResult | str:
    """Fetch all treatment sessions from SQLite as a fallback when Pinecone is unavailable."""
    sessions = db.fetch_treatment_sessions(
        db_path, params.patient_id, params.date_from, params.date_to
    )
    texts = [s.session_text_deidentified for s in sessions if s.session_text_deidentified]
    if not texts:
        return NO_RESULTS_MESSAGE

    logger.info(
        "SQLite fallback: retrieved %d session(s) for patient=%s", len(texts), params.patient_id
    )
    return RetrievalResult(
        chunks=texts, source_table='treatment_sessions_fallback', count=len(texts)
    )
