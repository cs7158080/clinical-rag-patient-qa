"""
router.py — Deterministic intent → retrieval strategy router. No LLM involved.

Maps a QueryParams object to a RouteDecision that specifies which retrieval
strategy to use. All routing logic lives here — business logic never decides
retrieval strategy; only this module does.

Strategies:
    'family_a_sqlite'         — fetch from family_a_sections (diagnosis / clinic_visit_summary)
    'treatment_sessions_sqlite' — fetch from treatment_sessions (treatment_plan summarize)
    'domain_sqlite'           — exact lookup in domain_findings by domain_name
    'pinecone'                — semantic search via Pinecone
    'compare_sqlite'          — fetch sessions before/after a reference date
    'error'                   — cannot route; error_message is set on the RouteDecision
"""

from dataclasses import dataclass

from app.storage.models import QueryParams

# ---------------------------------------------------------------------------
# Fixed clinical domain taxonomy (13 domains from Step 1)
# ---------------------------------------------------------------------------

FIXED_DOMAINS = {
    'פרגמטיקה ותקשורת',
    'הבנת שפה',
    'הבעת שפה',
    'לקסיקון',
    'תחביר',
    'מורפולוגיה',
    'התארגנות להבעת מלל מורכב',
    'מובנות הדיבור',
    'מודעות פונולוגית',
    'זיכרון שמיעתי',
    'אורל מוטור',
    'אכילה',
    'שטף',
}


# ---------------------------------------------------------------------------
# RouteDecision dataclass
# ---------------------------------------------------------------------------

@dataclass
class RouteDecision:
    """The result of the routing step.

    Attributes
    ----------
    strategy:      One of the strategy strings documented above.
    params:        The original QueryParams that produced this decision.
    error_message: Set only when strategy == 'error'. The user-facing Hebrew message.
    """
    strategy: str
    params: QueryParams
    error_message: str | None = None

# Helper
def _normalize_topic(topic: str | None) -> str | None:
    """Strip surrounding whitespace and quote/punctuation marks for tolerant matching.

    Cosmetic-only: does not correct spelling variants, only formatting noise
    (stray quotes, trailing punctuation, extra whitespace) an LLM might add.
    """
    if topic is None:
        return None
    return topic.strip().strip(' \t\n"\'\u05f4\u05f3.,:;')


# ---------------------------------------------------------------------------
# Public routing function
# ---------------------------------------------------------------------------

def route(params: QueryParams) -> RouteDecision:
    """Return a RouteDecision for the given QueryParams.

    Routing table:

    Intent              | template_type               | Topic             | Strategy
    --------------------|-----------------------------|-------------------|----------------------------
    summarize           | diagnosis / clinic_visit_summary | any          | family_a_sqlite
    summarize           | treatment_plan / null       | any               | treatment_sessions_sqlite
    compare_progress    | any (date_from set)         | any               | compare_sqlite
    compare_progress    | any (date_from null)        | any               | treatment_sessions_sqlite
    check_domain        | any                         | in FIXED_DOMAINS  | domain_sqlite
    check_domain        | any                         | NOT in taxonomy   | pinecone
    find_specific       | diagnosis / clinic_visit_summary | any          | family_a_sqlite
    find_specific       | any other                   | any               | pinecone
    (fallback)          | any                         | any               | pinecone
    """
    intent = params.intent
    template_type = params.template_type
    topic = params.topic

    # -- summarize -----------------------------------------------------------
    if intent == 'summarize':
        if template_type in ('diagnosis', 'clinic_visit_summary'):
            return RouteDecision(strategy='family_a_sqlite', params=params)
        return RouteDecision(strategy='treatment_sessions_sqlite', params=params)

    # -- compare_progress ----------------------------------------------------
    if intent == 'compare_progress':
        # No reference date → "progress over time": fetch all sessions chronologically
        if params.date_from is None:
            return RouteDecision(strategy='treatment_sessions_sqlite', params=params)
        return RouteDecision(strategy='compare_sqlite', params=params)
    
    # -- check_domain --------------------------------------------------------
    if intent == 'check_domain':
        normalized_topic = _normalize_topic(topic)
        if normalized_topic in FIXED_DOMAINS:
            params.topic = normalized_topic
            return RouteDecision(strategy='domain_sqlite', params=params)
        return RouteDecision(strategy='pinecone', params=params)



    # -- find_specific -------------------------------------------------------
    if intent == 'find_specific':
        if template_type in ('diagnosis', 'clinic_visit_summary'):
            return RouteDecision(strategy='family_a_sqlite', params=params)
        # No topic → nothing to embed; fetch sessions from SQLite directly
        if not topic:
            return RouteDecision(strategy='treatment_sessions_sqlite', params=params)
        return RouteDecision(strategy='pinecone', params=params)

    # -- fallback ------------------------------------------------------------
    return RouteDecision(strategy='pinecone', params=params)
