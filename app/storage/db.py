"""
db.py — SQLite CRUD module for the clinical RAG system.

All functions open and close their own sqlite3.connect(db_path) connection.
Use get_config() from app.config only if a caller passes no db_path;
callers are expected to supply the db_path directly.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.storage.models import (
    DomainFinding,
    FamilyAChunk,
    TreatmentGoalsChunk,
    TreatmentSessionChunk,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS family_a_sections (
    patient_id          TEXT NOT NULL,
    template_type       TEXT NOT NULL,
    session_date        TEXT NOT NULL,
    section             TEXT NOT NULL,
    text_deidentified   TEXT,
    source_file_path    TEXT,
    PRIMARY KEY (patient_id, template_type, session_date, section)
);

CREATE TABLE IF NOT EXISTS treatment_goals (
    patient_id                TEXT NOT NULL,
    session_date              TEXT NOT NULL,
    goals_text_deidentified   TEXT,
    pinecone_id               TEXT,
    source_file_path          TEXT,
    PRIMARY KEY (patient_id, session_date)
);

CREATE TABLE IF NOT EXISTS treatment_sessions (
    patient_id                  TEXT NOT NULL,
    session_date                TEXT NOT NULL,
    session_text_deidentified   TEXT,
    pinecone_id                 TEXT,
    source_file_path            TEXT,
    PRIMARY KEY (patient_id, session_date)
);

CREATE TABLE IF NOT EXISTS domain_findings (
    patient_id               TEXT NOT NULL,
    session_date             TEXT NOT NULL,
    domain_name              TEXT NOT NULL,
    domain_text_deidentified TEXT,
    parent_domain            TEXT,
    PRIMARY KEY (patient_id, session_date, domain_name)
);

CREATE TABLE IF NOT EXISTS ingested_files (
    file_path   TEXT PRIMARY KEY,
    file_hash   TEXT,
    ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS patient_metadata (
    patient_id  TEXT NOT NULL,
    field_name  TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (patient_id, field_name)
);
"""


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> None:
    """Create all 6 tables if they do not already exist."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_DDL)
        conn.commit()
    logger.info("Database initialised at %s", db_path)


# ---------------------------------------------------------------------------
# ingested_files helpers
# ---------------------------------------------------------------------------

def get_file_hash(db_path: str, file_path: str) -> Optional[str]:
    """Return the stored SHA-256 hash for *file_path*, or None if not found."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_hash FROM ingested_files WHERE file_path = ?",
            (file_path,),
        ).fetchone()
    return row[0] if row else None


def mark_file_ingested(db_path: str, file_path: str, file_hash: str) -> None:
    """Record that *file_path* has been successfully ingested with *file_hash*."""
    ingested_at = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ingested_files (file_path, file_hash, ingested_at)
                VALUES (?, ?, ?)
                """,
                (file_path, file_hash, ingested_at),
            )
            conn.commit()
        logger.info("Marked file as ingested: %s", file_path)
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to mark file ingested [%s]: %s", file_path, exc)
        raise


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def insert_family_a_chunk(db_path: str, chunk: FamilyAChunk) -> None:
    """INSERT OR REPLACE a FamilyAChunk into family_a_sections."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO family_a_sections
                    (patient_id, template_type, session_date, section,
                     text_deidentified, source_file_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.patient_id,
                    chunk.template_type,
                    chunk.session_date,
                    chunk.section,
                    chunk.text_deidentified,
                    chunk.source_file_path,
                ),
            )
            conn.commit()
        logger.info(
            "Inserted family_a_sections row: patient=%s type=%s date=%s section=%s",
            chunk.patient_id,
            chunk.template_type,
            chunk.session_date,
            chunk.section,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to insert family_a_chunk: %s", exc)
        raise


def insert_treatment_goals(db_path: str, chunk: TreatmentGoalsChunk) -> None:
    """INSERT OR REPLACE a TreatmentGoalsChunk into treatment_goals."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO treatment_goals
                    (patient_id, session_date, goals_text_deidentified,
                     pinecone_id, source_file_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chunk.patient_id,
                    chunk.session_date,
                    chunk.goals_text_deidentified,
                    chunk.pinecone_id,
                    chunk.source_file_path,
                ),
            )
            conn.commit()
        logger.info(
            "Inserted treatment_goals row: patient=%s date=%s",
            chunk.patient_id,
            chunk.session_date,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to insert treatment_goals: %s", exc)
        raise


def insert_treatment_session(db_path: str, chunk: TreatmentSessionChunk) -> None:
    """INSERT OR REPLACE a TreatmentSessionChunk into treatment_sessions."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO treatment_sessions
                    (patient_id, session_date, session_text_deidentified,
                     pinecone_id, source_file_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chunk.patient_id,
                    chunk.session_date,
                    chunk.session_text_deidentified,
                    chunk.pinecone_id,
                    chunk.source_file_path,
                ),
            )
            conn.commit()
        logger.info(
            "Inserted treatment_sessions row: patient=%s date=%s",
            chunk.patient_id,
            chunk.session_date,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to insert treatment_session: %s", exc)
        raise


def insert_domain_finding(db_path: str, finding: DomainFinding) -> None:
    """INSERT OR REPLACE a DomainFinding into domain_findings."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO domain_findings
                    (patient_id, session_date, domain_name, domain_text_deidentified, parent_domain)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    finding.patient_id,
                    finding.session_date,
                    finding.domain_name,
                    finding.domain_text_deidentified,
                    finding.parent_domain,
                ),
            )
            conn.commit()
        logger.info(
            "Inserted domain_findings row: patient=%s date=%s domain=%s parent=%s",
            finding.patient_id,
            finding.session_date,
            finding.domain_name,
            finding.parent_domain,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to insert domain_finding: %s", exc)
        raise


def upsert_patient_metadata(
    db_path: str, patient_id: str, field_name: str, value: str
) -> None:
    """INSERT OR REPLACE a single patient_metadata field."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO patient_metadata (patient_id, field_name, value)
                VALUES (?, ?, ?)
                """,
                (patient_id, field_name, value),
            )
            conn.commit()
        logger.info(
            "Upserted patient_metadata: patient=%s field=%s", patient_id, field_name
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to upsert patient_metadata: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Pinecone ID update helpers
# ---------------------------------------------------------------------------

def update_pinecone_id_goals(
    db_path: str, patient_id: str, session_date: str, pinecone_id: str
) -> None:
    """Set pinecone_id for a treatment_goals row."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE treatment_goals
                SET pinecone_id = ?
                WHERE patient_id = ? AND session_date = ?
                """,
                (pinecone_id, patient_id, session_date),
            )
            conn.commit()
        logger.info(
            "Updated pinecone_id for treatment_goals: patient=%s date=%s",
            patient_id,
            session_date,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to update pinecone_id for treatment_goals: %s", exc)
        raise


def update_pinecone_id_session(
    db_path: str, patient_id: str, session_date: str, pinecone_id: str
) -> None:
    """Set pinecone_id for a treatment_sessions row."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE treatment_sessions
                SET pinecone_id = ?
                WHERE patient_id = ? AND session_date = ?
                """,
                (pinecone_id, patient_id, session_date),
            )
            conn.commit()
        logger.info(
            "Updated pinecone_id for treatment_sessions: patient=%s date=%s",
            patient_id,
            session_date,
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to update pinecone_id for treatment_sessions: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Re-ingest helpers
# ---------------------------------------------------------------------------

def get_pinecone_ids_for_file(db_path: str, file_path: str) -> list[str]:
    """Return all non-null pinecone_ids from treatment_goals and treatment_sessions
    that were produced by *file_path*."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pinecone_id FROM treatment_goals
            WHERE source_file_path = ? AND pinecone_id IS NOT NULL
            UNION ALL
            SELECT pinecone_id FROM treatment_sessions
            WHERE source_file_path = ? AND pinecone_id IS NOT NULL
            """,
            (file_path, file_path),
        ).fetchall()
    return [row[0] for row in rows]


def delete_rows_for_file(db_path: str, file_path: str) -> None:
    """Delete all rows produced by *file_path* across all content tables in one transaction.

    domain_findings does not carry source_file_path (per schema). Its rows share
    (patient_id, session_date) with family_a_sections (diagnosis). We collect those
    pairs first — inside the same transaction — then delete domain_findings before
    deleting family_a_sections.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("BEGIN")

            # Step 1: collect (patient_id, session_date) pairs from family_a_sections
            # BEFORE deleting them — needed to cascade into domain_findings.
            pairs = conn.execute(
                """
                SELECT DISTINCT patient_id, session_date
                FROM family_a_sections
                WHERE source_file_path = ?
                """,
                (file_path,),
            ).fetchall()

            # Step 2: delete domain_findings for those pairs
            for pid, sdate in pairs:
                conn.execute(
                    "DELETE FROM domain_findings WHERE patient_id = ? AND session_date = ?",
                    (pid, sdate),
                )

            # Step 3: delete from the three tables that carry source_file_path
            conn.execute(
                "DELETE FROM family_a_sections WHERE source_file_path = ?",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM treatment_goals WHERE source_file_path = ?",
                (file_path,),
            )
            conn.execute(
                "DELETE FROM treatment_sessions WHERE source_file_path = ?",
                (file_path,),
            )
            conn.commit()

        logger.info("Deleted all rows for file: %s", file_path)
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.error("Failed to delete rows for file [%s]: %s", file_path, exc)
        raise


# ---------------------------------------------------------------------------
# Read helpers — family_a_sections
# ---------------------------------------------------------------------------

def fetch_family_a_sections(
    db_path: str,
    patient_id: str,
    template_type: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[FamilyAChunk]:
    """Fetch all family_a_sections rows for patient + template_type, with optional date filter."""
    query = """
        SELECT patient_id, template_type, session_date, section,
               text_deidentified, source_file_path
        FROM family_a_sections
        WHERE patient_id = ? AND template_type = ?
    """
    params: list = [patient_id, template_type]

    if date_from is not None:
        query += " AND session_date >= ?"
        params.append(date_from)
    if date_to is not None:
        query += " AND session_date <= ?"
        params.append(date_to)

    query += " ORDER BY session_date ASC, section ASC"

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        FamilyAChunk(
            patient_id=r[0],
            template_type=r[1],
            session_date=r[2],
            section=r[3],
            text_deidentified=r[4],
            source_file_path=r[5],
        )
        for r in rows
    ]


def fetch_latest_family_a(
    db_path: str, patient_id: str, template_type: str
) -> Optional[FamilyAChunk]:
    """Return the most recent family_a_sections row for patient + template_type."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT patient_id, template_type, session_date, section,
                   text_deidentified, source_file_path
            FROM family_a_sections
            WHERE patient_id = ? AND template_type = ?
            ORDER BY session_date DESC
            LIMIT 1
            """,
            (patient_id, template_type),
        ).fetchone()

    if row is None:
        return None
    return FamilyAChunk(
        patient_id=row[0],
        template_type=row[1],
        session_date=row[2],
        section=row[3],
        text_deidentified=row[4],
        source_file_path=row[5],
    )


# ---------------------------------------------------------------------------
# Read helpers — treatment_sessions
# ---------------------------------------------------------------------------

def fetch_treatment_sessions(
    db_path: str,
    patient_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[TreatmentSessionChunk]:
    """Fetch treatment_sessions for a patient, with optional date filter."""
    query = """
        SELECT patient_id, session_date, session_text_deidentified,
               pinecone_id, source_file_path
        FROM treatment_sessions
        WHERE patient_id = ?
    """
    params: list = [patient_id]

    if date_from is not None:
        query += " AND session_date >= ?"
        params.append(date_from)
    if date_to is not None:
        query += " AND session_date <= ?"
        params.append(date_to)

    query += " ORDER BY session_date ASC"

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        TreatmentSessionChunk(
            patient_id=r[0],
            session_date=r[1],
            session_text_deidentified=r[2],
            pinecone_id=r[3],
            source_file_path=r[4],
        )
        for r in rows
    ]


def fetch_latest_n_treatment_sessions(
    db_path: str,
    patient_id: str,
    limit: int,
) -> list[TreatmentSessionChunk]:
    """Fetch the N most recent treatment sessions, returned oldest-first."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, session_text_deidentified,
                   pinecone_id, source_file_path
            FROM treatment_sessions
            WHERE patient_id = ?
            ORDER BY session_date DESC
            LIMIT ?
            """,
            (patient_id, limit),
        ).fetchall()
    return [
        TreatmentSessionChunk(
            patient_id=r[0],
            session_date=r[1],
            session_text_deidentified=r[2],
            pinecone_id=r[3],
            source_file_path=r[4],
        )
        for r in reversed(rows)
    ]


def fetch_treatment_sessions_before(
    db_path: str, patient_id: str, date_ref: str
) -> list[TreatmentSessionChunk]:
    """Fetch treatment_sessions with session_date < date_ref."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, session_text_deidentified,
                   pinecone_id, source_file_path
            FROM treatment_sessions
            WHERE patient_id = ? AND session_date < ?
            ORDER BY session_date ASC
            """,
            (patient_id, date_ref),
        ).fetchall()

    return [
        TreatmentSessionChunk(
            patient_id=r[0],
            session_date=r[1],
            session_text_deidentified=r[2],
            pinecone_id=r[3],
            source_file_path=r[4],
        )
        for r in rows
    ]


def fetch_treatment_sessions_after(
    db_path: str, patient_id: str, date_ref: str
) -> list[TreatmentSessionChunk]:
    """Fetch treatment_sessions with session_date >= date_ref."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, session_text_deidentified,
                   pinecone_id, source_file_path
            FROM treatment_sessions
            WHERE patient_id = ? AND session_date >= ?
            ORDER BY session_date ASC
            """,
            (patient_id, date_ref),
        ).fetchall()

    return [
        TreatmentSessionChunk(
            patient_id=r[0],
            session_date=r[1],
            session_text_deidentified=r[2],
            pinecone_id=r[3],
            source_file_path=r[4],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Read helpers — treatment_goals
# ---------------------------------------------------------------------------

def fetch_latest_treatment_goals(
    db_path: str, patient_id: str, before_date: str
) -> Optional[TreatmentGoalsChunk]:
    """Return the most recent treatment_goals row with session_date <= before_date."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT patient_id, session_date, goals_text_deidentified,
                   pinecone_id, source_file_path
            FROM treatment_goals
            WHERE patient_id = ? AND session_date <= ?
            ORDER BY session_date DESC
            LIMIT 1
            """,
            (patient_id, before_date),
        ).fetchone()

    if row is None:
        return None
    return TreatmentGoalsChunk(
        patient_id=row[0],
        session_date=row[1],
        goals_text_deidentified=row[2],
        pinecone_id=row[3],
        source_file_path=row[4],
    )


# ---------------------------------------------------------------------------
# Read helpers — domain_findings
# ---------------------------------------------------------------------------

def fetch_domain_finding(
    db_path: str, patient_id: str, domain_name: str
) -> list[DomainFinding]:
    """Return all domain_findings rows for patient + domain_name (exact match)."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, domain_name, domain_text_deidentified, parent_domain
            FROM domain_findings
            WHERE patient_id = ? AND domain_name = ?
            ORDER BY session_date ASC
            """,
            (patient_id, domain_name),
        ).fetchall()

    return [
        DomainFinding(
            patient_id=r[0],
            session_date=r[1],
            domain_name=r[2],
            domain_text_deidentified=r[3],
            parent_domain=r[4],
        )
        for r in rows
    ]


def fetch_domain_by_parent(
    db_path: str, patient_id: str, parent_domain: str
) -> list[DomainFinding]:
    """Return all domain_findings rows where parent_domain matches.

    Used when querying "הבעת שפה" to get לקסיקון, תחביר, מורפולוגיה וכו'.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, domain_name, domain_text_deidentified, parent_domain
            FROM domain_findings
            WHERE patient_id = ? AND parent_domain = ?
            ORDER BY session_date ASC, domain_name ASC
            """,
            (patient_id, parent_domain),
        ).fetchall()

    return [
        DomainFinding(
            patient_id=r[0],
            session_date=r[1],
            domain_name=r[2],
            domain_text_deidentified=r[3],
            parent_domain=r[4],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Read helpers — patient_metadata
# ---------------------------------------------------------------------------

def get_patient_list(db_path: str) -> list[dict]:
    """Return [{patient_id, display_name}] for all patients that have a display_name field."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, value
            FROM patient_metadata
            WHERE field_name = 'display_name'
            ORDER BY value ASC
            """,
        ).fetchall()
    return [{"patient_id": r[0], "display_name": r[1]} for r in rows]


def get_patient_metadata(db_path: str, patient_id: str) -> dict[str, str]:
    """Return all patient_metadata fields for *patient_id* as {field_name: value}."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT field_name, value FROM patient_metadata WHERE patient_id = ?",
            (patient_id,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Read helpers — session dates
# ---------------------------------------------------------------------------

def get_treatment_session_dates(db_path: str, patient_id: str) -> list[str]:
    """Return session_dates for a patient from treatment_sessions, newest first."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT session_date
            FROM treatment_sessions
            WHERE patient_id = ?
            ORDER BY session_date DESC
            """,
            (patient_id,),
        ).fetchall()
    return [r[0] for r in rows]


def get_max_session_date(db_path: str, patient_id: str) -> Optional[str]:
    """Return the latest session_date for a patient, or None if no sessions exist."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(session_date) FROM treatment_sessions WHERE patient_id = ?",
            (patient_id,),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_latest_family_a_as_dict(
    db_path: str, patient_id: str, template_type: str
) -> dict[str, str] | None:
    """Return all sections of the most recent document of template_type as {section: text}.

    Returns None if no rows exist for this patient + template_type.
    """
    with sqlite3.connect(db_path) as conn:
        max_date_row = conn.execute(
            """
            SELECT MAX(session_date) FROM family_a_sections
            WHERE patient_id = ? AND template_type = ?
            """,
            (patient_id, template_type),
        ).fetchone()

    if max_date_row is None or max_date_row[0] is None:
        return None

    max_date = max_date_row[0]
    rows = fetch_family_a_sections(db_path, patient_id, template_type, max_date, max_date)
    if not rows:
        return None
    return {row.section: row.text_deidentified for row in rows}


def fetch_treatment_goals_in_range(
    db_path: str, patient_id: str, date_from: str, date_to: str
) -> list[TreatmentGoalsChunk]:
    """Return all treatment_goals rows in [date_from, date_to], oldest first."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT patient_id, session_date, goals_text_deidentified,
                   pinecone_id, source_file_path
            FROM treatment_goals
            WHERE patient_id = ? AND session_date >= ? AND session_date <= ?
            ORDER BY session_date ASC
            """,
            (patient_id, date_from, date_to),
        ).fetchall()
    return [
        TreatmentGoalsChunk(
            patient_id=r[0],
            session_date=r[1],
            goals_text_deidentified=r[2],
            pinecone_id=r[3],
            source_file_path=r[4],
        )
        for r in rows
    ]


def get_patient_files(db_path: str, patient_id: str) -> list[str]:
    """Return unique source_file_paths for a patient across all content tables, sorted by name."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT source_file_path FROM treatment_sessions
            WHERE patient_id = ? AND source_file_path IS NOT NULL
            UNION
            SELECT DISTINCT source_file_path FROM treatment_goals
            WHERE patient_id = ? AND source_file_path IS NOT NULL
            UNION
            SELECT DISTINCT source_file_path FROM family_a_sections
            WHERE patient_id = ? AND source_file_path IS NOT NULL
            ORDER BY source_file_path ASC
            """,
            (patient_id, patient_id, patient_id),
        ).fetchall()
    return [r[0] for r in rows]
