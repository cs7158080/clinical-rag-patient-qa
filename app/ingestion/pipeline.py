"""
pipeline.py — LlamaIndex Workflow for clinical document ingestion.

Workflow steps
--------------
parse_step         : Detect template type, check file hash, parse document.
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
from app.ingestion.adapter_a import detect_template_type, parse_family_a
from app.ingestion.adapter_b import parse_treatment_plan

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
    reason: str
    file_path: str


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
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._config = config
        self._reid_map = reid_map
        self._db_path = db_path
        self._pinecone_index = pinecone_index

    # ------------------------------------------------------------------
    # Step 1: Parse
    # ------------------------------------------------------------------

    @step
    async def parse_step(
        self, ctx: Context, ev: IngestFileEvent
    ) -> Union[ParsedEvent, StopEvent]:
        """Read the file, check its hash, detect template type, and parse."""
        file_path = ev.file_path

        # Hash check
        try:
            file_bytes = open(file_path, "rb").read()
        except OSError as exc:
            logger.error("parse_step: cannot read file %s: %s", file_path, exc)
            return StopEvent(result="error:unreadable")

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        existing_hash = get_file_hash(self._db_path, file_path)

        if existing_hash == file_hash:
            logger.info("parse_step: skipping unchanged file: %s", file_path)
            return StopEvent(result="skipped")

        # Detect template type from filename
        filename = os.path.basename(file_path)
        patient_folder_name = os.path.basename(os.path.dirname(file_path))

        try:
            if filename.startswith("סיכום אבחון") or filename.startswith("סיכום טיפול"):
                template_type = detect_template_type(filename)
                if template_type is None:
                    logger.warning("parse_step: unknown Family A template for: %s", file_path)
                    return StopEvent(result="unknown_template")
                parsed_data = parse_family_a(file_path, patient_folder_name, template_type)


            elif filename.startswith("תוכנית טיפול"):
                template_type = "treatment_plan"
                parsed_data = parse_treatment_plan(file_path, patient_folder_name)

            else:
                logger.warning("parse_step: unknown template type for file: %s", file_path)
                return StopEvent(result="unknown_template")

        except Exception as exc:
            logger.error("parse_step: parsing failed for %s: %s", file_path, exc)
            return StopEvent(result="error:parse_failed")

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
            return BlockedEvent(reason="name_conflict", file_path=file_path)

        # Ensure the patient entity exists in the re-id map; the patient_id
        # IS the key of the patient's PERSON entry (identity by construction).
        patient_token = add_entity(self._reid_map, "PERSON", patient_folder_name)
        patient_id = token_to_hash(patient_token)

        deid_data = dict(ev.parsed_data)
        # Preserve parent_domains mapping (needed for domain storage in store_family_a_step)
        if "parent_domains" not in deid_data:
            deid_data["parent_domains"] = {}

        # Apply de-identification to all text fields based on template type
        if ev.template_type in ("diagnosis", "clinic_visit_summary"):
            deid_sections: dict[str, str] = {}
            for section_key, text in deid_data.get("sections", {}).items():
                deid_sections[section_key] = deidentify_text(
                    text, patient_folder_name, variants, self._reid_map, file_path
                )
            deid_data["sections"] = deid_sections

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
        if ev.template_type in ("diagnosis", "clinic_visit_summary"):
            deid_data["sections"] = {
                k: sweep_reid_values(v, self._reid_map)
                for k, v in deid_data["sections"].items()
            }
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
            return BlockedEvent(
                reason=validation_result.failure_type or "validation_failed",
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
        return StopEvent(result=f"blocked:{ev.reason}")

    # ------------------------------------------------------------------
    # Step 4: Route
    # ------------------------------------------------------------------

    @step
    async def route_step(
        self, ctx: Context, ev: DeidentifiedEvent
    ) -> Union[FamilyAStoreEvent, TreatmentEmbedEvent]:
        """Route de-identified data to the correct storage branch."""
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
            return StopEvent(result="error:pinecone_delete_failed")

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
        return StopEvent(result="ok")

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
            return StopEvent(result="error:pinecone_delete_failed")

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

        mark_file_ingested(self._db_path, file_path, data["file_hash"])
        save_reid_map(
            os.path.join(self._config.data_dir, "reid_map.json"), self._reid_map
        )
        logger.info("store_treatment_step: ingested treatment plan: %s", file_path)
        return StopEvent(result="ok")


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------

async def run_ingestion(
    patients_roots: list,
    config,
    reid_map: dict,
    db_path: str,
    pinecone_index,
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

    Returns
    -------
    list of (file_path, result_str) tuples — one per .docx file processed.
    result_str is one of: "ok", "skipped", "blocked:<reason>", "error:<reason>".
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
                    results.append((file_path, str(result)))
                except Exception as exc:
                    logger.error(
                        "run_ingestion: unhandled error for %s: %s", file_path, exc
                    )
                    results.append((file_path, "error:unhandled"))

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


        
