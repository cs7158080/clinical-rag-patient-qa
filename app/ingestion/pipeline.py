"""
pipeline.py — LlamaIndex Workflow for clinical document ingestion.

Workflow steps
--------------
parse_step         : Classify by filename, check file hash, parse document.
deidentify_step    : Validate name variants, de-identify all text fields, run Pass 2 gate.
blocked_step       : Log blocked files and emit StopEvent.
route_step         : Route de-identified data to the correct storage branch.
store_family_a_step: Store Family A chunks (sections + domain findings) in SQLite.
embed_step         : Embed treatment plan chunks via Cohere.
store_treatment_step: Upsert vectors to Pinecone, store chunks in SQLite.

Module-level entry point
------------------------
run_ingestion(patients_root, config, reid_map, db_path, pinecone_index)
"""

import hashlib
import logging
import os
import traceback
from typing import Union

from llama_index.core.workflow import (  # type: ignore
    Context,
    Event,
    StartEvent,
    StopEvent,
    Workflow,
    step,
)

from app.deidentification.deid import (
    deidentify_text,
    sweep_reid_values,
    validate_name_variants,
)
from app.deidentification.ner import load_ner_model
from app.deidentification.reid_map import (
    add_entity,
    save as save_reid_map,
    token_to_hash,
)
from app.deidentification.validation import validate_deidentified
from app.storage.db import (
    delete_rows_for_file,
    get_file_hash,
    get_pinecone_ids_for_file,
    insert_domain_finding,
    insert_family_a_chunk,
    insert_treatment_goals,
    insert_treatment_session,
    mark_file_ingested,
    upsert_patient_metadata,
)
from app.storage.models import (
    DomainFinding,
    FamilyAChunk,
    TreatmentGoalsChunk,
    TreatmentSessionChunk,
)
from app.storage.pinecone_client import delete_vectors, get_cohere_embedding, upsert_vectors
from app.ingestion.adapter_a import parse_family_a
from app.ingestion.classify import TYPE_LABELS, classify_filename
from app.ingestion.adapter_b import parse_treatment_plan
from app.ingestion.dates import DateError
from app.ingestion.result import IngestResult, ParseBlocked

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event definitions
# ---------------------------------------------------------------------------

class IngestFileEvent(StartEvent):
    file_path: str


class ParsedEvent(Event):
    template_type: str
    parsed_data: dict


class DeidentifiedEvent(Event):
    template_type: str
    parsed_data: dict


class BlockedEvent(Event):
    reason: str        # code for the log (e.g. "ner_rescan")
    message: str       # Hebrew, shown in the UI
    file_path: str


# Hebrew UI messages. Generic on purpose: the found value goes to the log only
# (written by the Pass 2 checks in validation.py).
_UNKNOWN_TYPE_MSG = (
    "לא זוהה כמסמך קליני ולא נטען. אם זה אבחון, סיכום טיפול או תוכנית טיפול — "
    "יש לכלול זאת בשם הקובץ"
)
_CONFLICTING_TYPE_MSG = "שם הקובץ מתאים לכמה סוגי מסמכים ({labels}) — יש להשאיר בשם רק את הסוג הנכון"
_PRIVACY_SUFFIX = " — הקובץ לא נטען כדי להגן על הפרטיות"
_VALIDATION_MESSAGES = {
    "regex": "נמצא בטקסט פרט מזהה (ת.ז. / טלפון / דוא\"ל) שלא הוסר" + _PRIVACY_SUFFIX,
    "reid_map": "נמצא בטקסט שם או פרט מזהה מוכר שלא הוסר" + _PRIVACY_SUFFIX,
    "ner_rescan": "בדיקת הפרטיות מצאה שם שייתכן שלא הוסר" + _PRIVACY_SUFFIX,
}
_STALE_DELETE_MSG = "מחיקת הנתונים הקודמים של הקובץ נכשלה — הנתונים הקודמים נשמרו. נסי לטעון שוב"


class FamilyAStoreEvent(Event):
    parsed_data: dict


class TreatmentEmbedEvent(Event):
    parsed_data: dict


class EmbeddedEvent(Event):
    parsed_data: dict
    goals_embedded: list
    sessions_embedded: list


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class IngestionWorkflow(Workflow):
    """LlamaIndex Workflow that ingests a single .docx file end-to-end."""

    def __init__(
        self,
        config,
        reid_map: dict,
        db_path: str,
        pinecone_index,
        dry_run: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._config = config
        self._reid_map = reid_map
        self._db_path = db_path
        self._pinecone_index = pinecone_index
        # Dry run: stop after de-id + Pass 2 (route_step). No DB, Cohere or Pinecone access.
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Step 1: Parse
    # ------------------------------------------------------------------

    @step
    async def parse_step(
        self, ctx: Context, ev: IngestFileEvent
    ) -> Union[ParsedEvent, StopEvent]:
        """Read the file, check its hash, detect template type, and parse."""
        file_path = ev.file_path
        filename = os.path.basename(file_path)
        patient_folder_name = os.path.basename(os.path.dirname(file_path))

        # Document type from the filename only (PLAN.md 3-X) — before reading,
        # so an unrecognised file is never opened.
        matched = classify_filename(filename)
        if not matched:
            logger.info("parse_step: not a recognised document type, ignored: %s", file_path)
            return StopEvent(result=IngestResult("ignored", _UNKNOWN_TYPE_MSG))
        if len(matched) > 1:
            logger.warning("parse_step: filename matches several types %s: %s", matched, file_path)
            labels = ", ".join(TYPE_LABELS[t] for t in matched)
            return StopEvent(result=IngestResult(
                "blocked", _CONFLICTING_TYPE_MSG.format(labels=labels)))
        [template_type] = matched

        # Hash check
        try:
            file_bytes = open(file_path, "rb").read()
        except OSError as exc:
            logger.error("parse_step: cannot read file %s: %s", file_path, exc)
            return StopEvent(result=IngestResult(
                "error", "לא ניתן לקרוא את הקובץ — ייתכן שהוא פתוח ב-Word. סגרי אותו ונסי שוב"))

        file_hash = hashlib.sha256(file_bytes).hexdigest()

        if not self._dry_run and get_file_hash(self._db_path, file_path) == file_hash:
            logger.info("parse_step: skipping unchanged file: %s", file_path)
            return StopEvent(result=IngestResult("skipped"))

        try:
            if template_type == "treatment_plan":
                parsed_data = parse_treatment_plan(file_path, patient_folder_name)
            else:
                parsed_data = parse_family_a(file_path, patient_folder_name, template_type)

        except (ParseBlocked, DateError) as exc:
            logger.warning("parse_step: blocked %s: %s", file_path, exc)
            return StopEvent(result=IngestResult("blocked", str(exc)))
        except Exception:
            logger.exception("parse_step: parsing failed for %s", file_path)
            return StopEvent(result=IngestResult(
                "error", "שגיאה בקריאת תוכן המסמך — הפרטים נרשמו ביומן"))

        parsed_data["file_hash"] = file_hash

        return ParsedEvent(template_type=template_type, parsed_data=parsed_data)

    # ------------------------------------------------------------------
    # Step 2: De-identify
    # ------------------------------------------------------------------

    @step
    async def deidentify_step(
        self, ctx: Context, ev: ParsedEvent
    ) -> Union[DeidentifiedEvent, BlockedEvent]:
        """Validate name variants, apply Pass 1 de-identification, run Pass 2 gate."""

        patient_folder_name = ev.parsed_data["patient_folder_name"]
        file_path = ev.parsed_data["file_path"]

        # Validate name variants from the three sources
        header_name = ev.parsed_data.get("header", {}).get("name")
        filename = os.path.basename(file_path)
        variants, had_conflict = validate_name_variants(
            patient_folder_name, filename, header_name
        )

        if had_conflict:
            logger.error(
                "deidentify_step: name conflict detected for %s "
                "(conflict type: last_name_mismatch)",
                file_path,
            )
            return BlockedEvent(
                reason="name_conflict",
                message="שם המטופל בכותרת הקובץ שונה משם התיקייה",
                file_path=file_path,
            )

        # Ensure the patient entity exists in the re-id map; the patient_id
        # IS the key of the patient's PERSON entry (identity by construction).
        patient_token = add_entity(self._reid_map, "PERSON", patient_folder_name)
        patient_id = token_to_hash(patient_token)

        deid_data = dict(ev.parsed_data)
        # Preserve parent_domains mapping (needed for domain storage in store_family_a_step)
        if "parent_domains" not in deid_data:
            deid_data["parent_domains"] = {}

        # Apply de-identification to all text fields based on template type.
        # Sections: Family A document sections, or treatment-plan header fields (T2 5-ד)
        deid_data["sections"] = {
            section_key: deidentify_text(
                text, patient_folder_name, variants, self._reid_map, file_path
            )
            for section_key, text in deid_data.get("sections", {}).items()
        }

        if ev.template_type in ("diagnosis", "clinic_visit_summary"):
            deid_domains: dict[str, str] = {}
            for domain, text in deid_data.get("domains", {}).items():
                deid_domains[domain] = deidentify_text(
                    text, patient_folder_name, variants, self._reid_map, file_path
                )
            deid_data["domains"] = deid_domains

        elif ev.template_type == "treatment_plan":
            deid_goals = []
            for row in deid_data.get("goals_rows", []):
                deid_goals.append({
                    **row,
                    "goals_text": deidentify_text(
                        row["goals_text"],
                        patient_folder_name,
                        variants,
                        self._reid_map,
                        file_path,
                    ),
                })
            deid_data["goals_rows"] = deid_goals

            deid_sessions = []
            for block in deid_data.get("session_blocks", []):
                deid_sessions.append({
                    **block,
                    "session_text": deidentify_text(
                        block["session_text"],
                        patient_folder_name,
                        variants,
                        self._reid_map,
                        file_path,
                    ),
                })
            deid_data["session_blocks"] = deid_sessions

        # Final deterministic sweep — the reid map now contains everything
        # discovered in ALL fields of this file (and previous files), so a
        # name NER missed in one block but caught in another is still replaced.
        deid_data["sections"] = {
            k: sweep_reid_values(v, self._reid_map)
            for k, v in deid_data["sections"].items()
        }
        if ev.template_type in ("diagnosis", "clinic_visit_summary"):
            deid_data["domains"] = {
                k: sweep_reid_values(v, self._reid_map)
                for k, v in deid_data["domains"].items()
            }
        else:
            deid_data["goals_rows"] = [
                {**r, "goals_text": sweep_reid_values(r["goals_text"], self._reid_map)}
                for r in deid_data["goals_rows"]
            ]
            deid_data["session_blocks"] = [
                {**b, "session_text": sweep_reid_values(b["session_text"], self._reid_map)}
                for b in deid_data["session_blocks"]
            ]

        # Pass 2 validation — collect all de-identified text fields
        all_texts = (
            list(deid_data.get("sections", {}).values())
            + list(deid_data.get("domains", {}).values())
            + [r["goals_text"] for r in deid_data.get("goals_rows", [])]
            + [b["session_text"] for b in deid_data.get("session_blocks", [])]
        )
        all_text = " ".join(t for t in all_texts if t)

        validation_result = validate_deidentified(all_text, self._reid_map)
        if not validation_result.passed:
            logger.error(
                "deidentify_step: Pass 2 validation failed for %s: %s",
                file_path,
                validation_result.failure_type,
            )
            reason = validation_result.failure_type or "validation_failed"
            return BlockedEvent(
                reason=reason,
                message=_VALIDATION_MESSAGES.get(reason, "בדיקת הפרטיות נכשלה" + _PRIVACY_SUFFIX),
                file_path=file_path,
            )

        deid_data["patient_id"] = patient_id
        return DeidentifiedEvent(template_type=ev.template_type, parsed_data=deid_data)

    # ------------------------------------------------------------------
    # Step 3: Handle blocked files
    # ------------------------------------------------------------------

    @step
    async def blocked_step(self, ctx: Context, ev: BlockedEvent) -> StopEvent:
        """Log and terminate processing for a blocked file."""
        logger.error(
            "blocked_step: file blocked from ingestion — path=%s reason=%s",
            ev.file_path,
            ev.reason,
        )
        return StopEvent(result=IngestResult("blocked", ev.message))

    # ------------------------------------------------------------------
    # Step 4: Route
    # ------------------------------------------------------------------

    @step
    async def route_step(
        self, ctx: Context, ev: DeidentifiedEvent
    ) -> Union[FamilyAStoreEvent, TreatmentEmbedEvent, StopEvent]:
        """Route de-identified data to the correct storage branch (dry run: stop here)."""
        if self._dry_run:
            data = ev.parsed_data
            is_family_a = ev.template_type in ("diagnosis", "clinic_visit_summary")
            return StopEvent(result=IngestResult(
                "ok",
                warnings=data.get("warnings", []),
                counts={
                    # non-empty only — what reaches the LLM (T1 6-ב)
                    "sections": sum(1 for t in data.get("sections", {}).values() if t),
                    "domains": sum(1 for t in data.get("domains", {}).values() if t),
                    "goals": len(data.get("goals_rows", [])),
                    "sessions": len(data.get("session_blocks", [])),
                },
                document_date=data["header"]["date"] if is_family_a else "",
            ))
        if ev.template_type in ("diagnosis", "clinic_visit_summary"):
            return FamilyAStoreEvent(parsed_data=ev.parsed_data)
        return TreatmentEmbedEvent(parsed_data=ev.parsed_data)

    # ------------------------------------------------------------------
    # Stale-data cleanup for re-ingested files
    # ------------------------------------------------------------------

    def _delete_stale_data(self, file_path: str) -> bool:
        """Delete previously stored rows/vectors for a re-ingested file.

        Called at the start of the store steps — after parsing, de-id,
        validation, and embedding have all succeeded — so a blocked or
        failed re-ingest keeps the previous good data. Returns False if
        Pinecone deletion failed (caller must abort).
        """
        if get_file_hash(self._db_path, file_path) is None:
            return True
        try:
            prior_ids = get_pinecone_ids_for_file(self._db_path, file_path)
            if prior_ids:
                delete_vectors(self._pinecone_index, prior_ids)
        except Exception as exc:
            logger.error(
                "delete_stale_data: Pinecone deletion failed for %s: %s — aborting",
                file_path,
                exc,
            )
            return False
        delete_rows_for_file(self._db_path, file_path)
        return True

    # ------------------------------------------------------------------
    # Step 5a: Store Family A (SQLite only — no embeddings)
    # ------------------------------------------------------------------

    @step
    async def store_family_a_step(
        self, ctx: Context, ev: FamilyAStoreEvent
    ) -> StopEvent:
        """Write Family A sections and domain findings to SQLite."""

        data = ev.parsed_data
        patient_id: str = data["patient_id"]
        template_type: str = data["template_type"]
        session_date: str = data["header"].get("date") or ""
        file_path: str = data["file_path"]
        header: dict = data["header"]

        if not self._delete_stale_data(file_path):
            return StopEvent(result=IngestResult("error", _STALE_DELETE_MSG))

        # Upsert static patient metadata
        if header.get("date_of_birth"):
            upsert_patient_metadata(
                self._db_path, patient_id, "date_of_birth", header["date_of_birth"]
            )
        if header.get("hmo_name"):
            upsert_patient_metadata(
                self._db_path, patient_id, "hmo_name", header["hmo_name"]
            )
        if header.get("national_id"):
            upsert_patient_metadata(
                self._db_path, patient_id, "national_id", header["national_id"]
            )
        upsert_patient_metadata(
            self._db_path, patient_id, "display_name", data["patient_folder_name"]
        )

        # Insert section chunks
        for section_key, text in data.get("sections", {}).items():
            chunk = FamilyAChunk(
                patient_id=patient_id,
                template_type=template_type,
                session_date=session_date,
                section=section_key,
                text_deidentified=text,
                source_file_path=file_path,
            )
            insert_family_a_chunk(self._db_path, chunk)

        # Insert domain findings (both Family A types)
        parent_domains: dict = data.get("parent_domains", {})

        for domain_name, domain_text in data.get("domains", {}).items():
            finding = DomainFinding(
                patient_id=patient_id,
                session_date=session_date,
                domain_name=domain_name,
                domain_text_deidentified=domain_text,
                parent_domain=parent_domains.get(domain_name),
            )
            insert_domain_finding(self._db_path, finding)

        mark_file_ingested(self._db_path, file_path, data["file_hash"])
        save_reid_map(
            os.path.join(self._config.data_dir, "reid_map.json"), self._reid_map
        )
        logger.info("store_family_a_step: ingested Family A file: %s", file_path)
        return StopEvent(result=IngestResult("ok", warnings=data.get("warnings", [])))

    # ------------------------------------------------------------------
    # Step 5b: Embed treatment plan chunks
    # ------------------------------------------------------------------

    @step
    async def embed_step(
        self, ctx: Context, ev: TreatmentEmbedEvent
    ) -> EmbeddedEvent:
        """Produce Cohere embeddings for all goals rows and session blocks."""
        data = ev.parsed_data
        patient_id: str = data["patient_id"]
        config = self._config

        goals_embedded: list[dict] = []
        for row in data.get("goals_rows", []):
            vector = get_cohere_embedding(
                row["goals_text"], config.cohere_api_key, config.cohere.model
            )
            pinecone_id = f"{patient_id}_goals_{row['session_date']}"
            goals_embedded.append({
                "session_date": row["session_date"],
                "goals_text": row["goals_text"],
                "vector": vector,
                "pinecone_id": pinecone_id,
            })

        sessions_embedded: list[dict] = []
        for block in data.get("session_blocks", []):
            vector = get_cohere_embedding(
                block["session_text"], config.cohere_api_key, config.cohere.model
            )
            pinecone_id = f"{patient_id}_session_{block['session_date']}"
            sessions_embedded.append({
                "session_date": block["session_date"],
                "session_text": block["session_text"],
                "vector": vector,
                "pinecone_id": pinecone_id,
            })

        # Upsert static patient metadata from treatment plan header
        header = data.get("header", {})
        if header.get("date_of_birth"):
            upsert_patient_metadata(
                self._db_path, patient_id, "date_of_birth", header["date_of_birth"]
            )
        upsert_patient_metadata(
            self._db_path, patient_id, "display_name", data["patient_folder_name"]
        )

        return EmbeddedEvent(
            parsed_data=data,
            goals_embedded=goals_embedded,
            sessions_embedded=sessions_embedded,
        )

    # ------------------------------------------------------------------
    # Step 5c: Store treatment plan (Pinecone + SQLite)
    # ------------------------------------------------------------------

    @step
    async def store_treatment_step(
        self, ctx: Context, ev: EmbeddedEvent
    ) -> StopEvent:
        """Upsert embeddings to Pinecone and write chunks to SQLite."""
        data = ev.parsed_data
        patient_id: str = data["patient_id"]
        file_path: str = data["file_path"]

        if not self._delete_stale_data(file_path):
            return StopEvent(result=IngestResult("error", _STALE_DELETE_MSG))

        # Build Pinecone vector list
        pinecone_vectors: list[dict] = []
        for item in ev.goals_embedded:
            pinecone_vectors.append({
                "id": item["pinecone_id"],
                "values": item["vector"],
                "metadata": {
                    "patient_id": patient_id,
                    "session_date": item["session_date"],
                    "session_date_num": int(item["session_date"].replace("-", "")),
                    "chunk_type": "goals_row",
                },
            })
        for item in ev.sessions_embedded:
            pinecone_vectors.append({
                "id": item["pinecone_id"],
                "values": item["vector"],
                "metadata": {
                    "patient_id": patient_id,
                    "session_date": item["session_date"],
                    "session_date_num": int(item["session_date"].replace("-", "")),
                    "chunk_type": "session_summary",
                },
            })

        if pinecone_vectors:
            upsert_vectors(self._pinecone_index, pinecone_vectors)

        # Write goals rows to SQLite
        for item in ev.goals_embedded:
            chunk = TreatmentGoalsChunk(
                patient_id=patient_id,
                session_date=item["session_date"],
                goals_text_deidentified=item["goals_text"],
                pinecone_id=item["pinecone_id"],
                source_file_path=file_path,
            )
            insert_treatment_goals(self._db_path, chunk)

        # Write session blocks to SQLite
        for item in ev.sessions_embedded:
            chunk = TreatmentSessionChunk(
                patient_id=patient_id,
                session_date=item["session_date"],
                session_text_deidentified=item["session_text"],
                pinecone_id=item["pinecone_id"],
                source_file_path=file_path,
            )
            insert_treatment_session(self._db_path, chunk)

        # Plan header fields (19a, O4.1) — document_sections rows, one date (T2 5-ג)
        for section_key, text in data.get("sections", {}).items():
            insert_family_a_chunk(self._db_path, FamilyAChunk(
                patient_id=patient_id,
                template_type="treatment_plan",
                session_date=data["sections_date"],
                section=section_key,
                text_deidentified=text,
                source_file_path=file_path,
            ))

        mark_file_ingested(self._db_path, file_path, data["file_hash"])
        save_reid_map(
            os.path.join(self._config.data_dir, "reid_map.json"), self._reid_map
        )
        logger.info("store_treatment_step: ingested treatment plan: %s", file_path)
        return StopEvent(result=IngestResult("ok", warnings=data.get("warnings", [])))


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------

def _failed_step(exc: BaseException) -> str:
    """Name of the workflow step the exception came from (last *_step frame), or 'unknown'."""
    names = [f.name for f in traceback.extract_tb(exc.__traceback__) if f.name.endswith("_step")]
    return names[-1] if names else "unknown"


async def run_ingestion(
    patients_roots: list,
    config,
    reid_map: dict,
    db_path: str,
    pinecone_index,
    dry_run: bool = False,
) -> list:
    """Walk every patients root directory and ingest every .docx file found.

    For each patient sub-folder, every .docx file is submitted to
    IngestionWorkflow.  Files whose SHA-256 hash is unchanged are skipped
    automatically by parse_step.  A patient folder with the same name in
    more than one root is treated as the same patient.

    Parameters
    ----------
    patients_roots:
        List of root directories, each containing one sub-folder per patient.
    config:
        Application config object (AppConfig or equivalent).
    reid_map:
        Mutable re-identification map dict (loaded from disk before calling).
    db_path:
        Absolute path to the SQLite database file.
    pinecone_index:
        Initialised Pinecone Index object (from init_pinecone).
    dry_run:
        Stop after de-identification + Pass 2; nothing is stored or sent to
        Cohere / Pinecone, and the file hash is not checked (scripts/diagnose_ingestion.py).

    Returns
    -------
    list of (file_path, IngestResult) tuples — one per .docx file processed.
    """
    # De-id gate: the NER model is mandatory in production (fail-closed).
    try:
        load_ner_model(config.models_dir)
    except Exception as exc:
        if config.run_mode == "production":
            logger.error("run_ingestion: NER model failed to load: %s", exc)
            raise RuntimeError(
                "מודל ה-NER אינו זמין — טעינת קבצים חסומה במצב production. "
                "יש להריץ setup.bat כדי להוריד ולהמיר את המודל."
            ) from exc
        logger.warning(
            "run_ingestion: run_mode=test — NER model unavailable (%s). "
            "Proceeding WITHOUT NER de-identification!",
            exc,
        )

    workflow = IngestionWorkflow(
        config=config,
        reid_map=reid_map,
        db_path=db_path,
        pinecone_index=pinecone_index,
        dry_run=dry_run,
        timeout=300,
    )

    results: list = []

    for patients_root in patients_roots:
        for patient_folder in os.listdir(patients_root):
            patient_path = os.path.join(patients_root, patient_folder)
            if not os.path.isdir(patient_path):
                continue

            for filename in os.listdir(patient_path):
                if not filename.endswith(".docx"):
                    continue
                # Skip Word temp/lock files
                if filename.startswith("~$") or filename.startswith("~"):
                    continue

                file_path = os.path.join(patient_path, filename)
                try:
                    result = await workflow.run(file_path=file_path)
                    logger.info("run_ingestion: %s → %s", file_path, result)
                except Exception as exc:
                    step_name = _failed_step(exc)
                    logger.exception(
                        "run_ingestion: unhandled error in %s for %s", step_name, file_path
                    )
                    result = IngestResult(
                        "error", f"שגיאה לא צפויה (שלב: {step_name}) — הפרטים נרשמו ביומן"
                    )
                results.append((file_path, result))

    return results


if __name__ == "__main__":
    import asyncio
    from app.config import get_config
    from app.storage.pinecone_client import init_pinecone
    from app.deidentification.reid_map import load as load_reid_map

    async def run():
        config = get_config()
        db_path = os.path.join(config.data_dir, "clinical_rag.db")
        reid_map_path = os.path.join(config.data_dir, "reid_map.json")
        reid_map = load_reid_map(reid_map_path)
        pinecone_index = init_pinecone(config.pinecone_api_key, config.pinecone.index_name)

        result = await run_ingestion(
            patients_roots=config.patients_roots,
            config=config,
            reid_map=reid_map,
            db_path=db_path,
            pinecone_index=pinecone_index,
        )
        return result

    asyncio.run(run())


        
