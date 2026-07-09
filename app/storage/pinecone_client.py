"""
pinecone_client.py — Pinecone vector store client and Cohere embedding helpers.

Responsibilities:
- Initialise a Pinecone Index object (returned to callers; not cached here).
- Upsert, query, and delete vectors.
- Produce Cohere embeddings for documents and queries.

All Pinecone calls use the Pinecone SDK v3+ API (pinecone.Pinecone / Index).
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pinecone helpers
# ---------------------------------------------------------------------------

def init_pinecone(api_key: str, index_name: str) -> Any:
    """Return a Pinecone Index object connected to *index_name*.

    Parameters
    ----------
    api_key:    Pinecone API key (from AppConfig / environment).
    index_name: Name of the existing Pinecone index.
    """
    from pinecone import Pinecone  # type: ignore

    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)
    logger.info("Connected to Pinecone index: %s", index_name)
    return index


def upsert_vectors(index: Any, vectors: list[dict]) -> None:
    """Upsert a list of vector dicts into *index*.

    Each dict must contain:
        id       (str)         — unique vector ID
        values   (list[float]) — embedding values
        metadata (dict)        — arbitrary metadata stored alongside the vector

    Parameters
    ----------
    index:   A Pinecone Index object returned by :func:`init_pinecone`.
    vectors: List of vector dicts.
    """
    if not vectors:
        logger.info("upsert_vectors called with empty list — nothing to upsert")
        return

    # Pinecone SDK v3 accepts a list of dicts directly (with 'id', 'values',
    # 'metadata' keys) or a list of tuples. We pass dicts.
    index.upsert(vectors=vectors)
    logger.info("Upserted %d vectors to Pinecone", len(vectors))


def query_pinecone(
    index: Any,
    query_vector: list[float],
    patient_id: str,
    top_k: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """Query Pinecone with a mandatory patient_id pre-filter and an optional date range.

    Parameters
    ----------
    index:        Pinecone Index object.
    query_vector: Embedding of the query text.
    patient_id:   Mandatory equality filter — only this patient's vectors are searched.
    top_k:        Maximum number of matches to return (default 10).
    date_from:    Lower bound for session_date (ISO 8601, inclusive). Optional.
    date_to:      Upper bound for session_date (ISO 8601, inclusive). Optional.

    Returns
    -------
    List of match dicts, each containing {id, score, metadata}.
    """
    # Build filter — patient_id is always required.
    # Date range uses session_date_num (YYYYMMDD int) because Pinecone $gte/$lte
    # only work with numbers, not strings.
    filter_dict: dict = {"patient_id": {"$eq": patient_id}}

    if date_from is not None and date_to is not None:
        from_num = int(date_from.replace("-", ""))
        to_num = int(date_to.replace("-", ""))
        filter_dict["session_date_num"] = {"$gte": from_num, "$lte": to_num}
    elif date_from is not None:
        filter_dict["session_date_num"] = {"$gte": int(date_from.replace("-", ""))}
    elif date_to is not None:
        filter_dict["session_date_num"] = {"$lte": int(date_to.replace("-", ""))}

    response = index.query(
        vector=query_vector,
        filter=filter_dict,
        top_k=top_k,
        include_metadata=True,
    )

    # Pinecone SDK v3 returns a QueryResponse object; matches is a list of ScoredVector
    matches = response.matches if hasattr(response, "matches") else response.get("matches", [])
    logger.info(
        "Pinecone query returned %d matches (patient=%s, date_from=%s, date_to=%s)",
        len(matches),
        patient_id,
        date_from,
        date_to,
    )

    return [
        {
            "id": m.id if hasattr(m, "id") else m["id"],
            "score": m.score if hasattr(m, "score") else m["score"],
            "metadata": dict(m.metadata) if hasattr(m, "metadata") and m.metadata else (m.get("metadata", {}) if isinstance(m, dict) else {}),
        }
        for m in matches
    ]


def delete_vectors(index: Any, ids: list[str]) -> None:
    """Delete vectors by ID from *index*.

    Parameters
    ----------
    index: Pinecone Index object.
    ids:   List of vector IDs to delete.
    """
    if not ids:
        logger.info("delete_vectors called with empty id list — nothing to delete")
        return

    index.delete(ids=ids)
    logger.info("Deleted %d vectors from Pinecone", len(ids))


# ---------------------------------------------------------------------------
# Cohere embedding helpers
# ---------------------------------------------------------------------------

def get_cohere_embedding(text: str, api_key: str, model: str) -> list[float]:
    """Return a Cohere embedding for *text* using input_type='search_document'.

    Use this when embedding chunks during ingestion.

    Parameters
    ----------
    text:    The de-identified document text to embed.
    api_key: Cohere API key.
    model:   Cohere embedding model name (e.g. 'embed-multilingual-v3.0').
    """
    import cohere  # type: ignore

    co = cohere.ClientV2(api_key)
    response = co.embed(texts=[text], model=model, input_type="search_document", embedding_types=["float"])
    embedding: list[float] = response.embeddings.float_[0]
    logger.info("Cohere document embedding produced (model=%s, dim=%d)", model, len(embedding))
    return embedding


def get_cohere_query_embedding(text: str, api_key: str, model: str) -> list[float]:
    """Return a Cohere embedding for *text* using input_type='search_query'.

    Use this when embedding a user query at retrieval time.

    Parameters
    ----------
    text:    The query text to embed.
    api_key: Cohere API key.
    model:   Cohere embedding model name (e.g. 'embed-multilingual-v3.0').
    """
    import cohere  # type: ignore

    co = cohere.ClientV2(api_key)
    logger.warning("model=%r", model)
    response = co.embed(texts=[text], model=model, input_type="search_query", embedding_types=["float"])
    embedding: list[float] = response.embeddings.float_[0]
    logger.info("Cohere query embedding produced (model=%s, dim=%d)", model, len(embedding))
    return embedding
