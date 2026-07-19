# LlamaIndex Generate Summary Workflow.
from llama_index.core.workflow import Workflow, StartEvent, StopEvent, step, Context, Event
from llama_index.llms.anthropic import Anthropic as LlamaAnthropic
from typing import Union
import os
import io
import json
import hashlib
import logging

from app.storage import db
from app.storage.models import FamilyAChunk, DomainFinding
from app.deidentification.reid_map import reidentify_text, load as load_reid_map, reverse_lookup
from app.prompts.generation import (
    GENERATE_SUMMARY_PROMPT,
    GenerationParseError,
    parse_generated_sections,
    SECTION_KEYS,
    DOMAINS_KEY,
)
from app.prompts.qa import NO_SESSION_MESSAGE, ERROR_MESSAGE
from app.ingestion.adapter_a import PARENT_DOMAINS
from app.generation.docx_builder import build_summary_docx
from app.config import AppConfig

logger = logging.getLogger(__name__)

NO_BASE_DOCUMENT_MESSAGE = "לא נמצא מסמך בסיס (אבחון או סיכום טיפול קודם) — לא ניתן לייצר סיכום"
PARSE_FAILURE_MESSAGE = (
    "יצירת הסיכום נכשלה — תשובת המודל לא התקבלה במבנה תקין ולא נוצר קובץ. "
    "נסי שוב; הפלט הגולמי נשמר בלוג."
)
FALLBACK_TEMPLATE_PATH = os.path.join('tamplates', 'טמפליט אבחון בנים.docx')
GENERATED_FILE_PREFIX = 'סיכום טיפול'


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class GenerateStartEvent(StartEvent):
    patient_id: str
    date_from: str   # first session in range (ISO 8601)
    date_to: str     # last session in range (ISO 8601)


class SourcesEvent(Event):
    sessions_text: str      # all sessions in range, joined by \n---\n
    goals_text: str         # all goals rows in range, joined by \n---\n
    base_sections: dict     # {section_key: text} of the base document (no תמצית)
    base_domains: dict      # {domain_name: text} of the base document's findings
    base_source_path: str   # physical file the base document came from ('' if unknown)
    patient_id: str
    date_from: str
    date_to: str


class TokenizedDocEvent(Event):
    sections: dict
    domains: dict
    base_source_path: str
    patient_id: str
    date_to: str   # used as session_date when writing to SQLite


class ReidentifiedDocEvent(Event):
    sections: dict             # re-identified — for the .docx body only
    domains: dict              # re-identified — for the .docx body only
    tokenized_sections: dict   # de-identified — the ONLY version stored in SQLite
    tokenized_domains: dict    # de-identified — the ONLY version stored in SQLite
    base_source_path: str
    patient_id: str
    date_to: str
    patient_name: str
    national_id: str
    date_of_birth: str


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class GenerateSummaryWorkflow(Workflow):
    def __init__(self, config: AppConfig, db_path: str, reid_map_path: str, **kwargs):
        super().__init__(**kwargs)
        self._config = config
        self._db_path = db_path
        self._reid_map_path = reid_map_path
        self._llm = LlamaAnthropic(
            model=config.anthropic.generation_model,
            api_key=config.anthropic_api_key,
            temperature=config.anthropic.temperature_generation,
            max_tokens=config.anthropic.max_tokens_generation,
        )

    @step
    async def fetch_sources_step(
        self, ctx: Context, ev: GenerateStartEvent
    ) -> Union[SourcesEvent, StopEvent]:
        # Sessions in range
        sessions = db.fetch_treatment_sessions(
            self._db_path, ev.patient_id, ev.date_from, ev.date_to
        )
        if not sessions:
            return StopEvent(result=NO_SESSION_MESSAGE)

        sessions_text = '\n---\n'.join(s.session_text_deidentified for s in sessions)

        # Goals in range
        goals_rows = db.fetch_treatment_goals_in_range(
            self._db_path, ev.patient_id, ev.date_from, ev.date_to
        )
        goals_text = '\n---\n'.join(g.goals_text_deidentified for g in goals_rows) if goals_rows else ''

        # Base document: latest clinic_visit_summary → latest diagnosis → error
        base_doc = db.fetch_latest_family_a_doc(
            self._db_path, ev.patient_id, 'clinic_visit_summary'
        )
        if base_doc is None:
            logger.info(
                "No clinic_visit_summary found for patient=%s, falling back to diagnosis",
                ev.patient_id,
            )
            base_doc = db.fetch_latest_family_a_doc(
                self._db_path, ev.patient_id, 'diagnosis'
            )
        if base_doc is None:
            logger.error("No base document found for patient=%s", ev.patient_id)
            return StopEvent(result=NO_BASE_DOCUMENT_MESSAGE)

        # The 4 generated sections (תמצית is dropped from the generated document)
        base_sections = {k: base_doc['sections'].get(k, '') for k in SECTION_KEYS}

        # Domain findings of the base document's date
        findings = db.fetch_domain_findings_for_date(
            self._db_path, ev.patient_id, base_doc['session_date']
        )
        base_domains = {f.domain_name: f.domain_text_deidentified for f in findings}

        return SourcesEvent(
            sessions_text=sessions_text,
            goals_text=goals_text,
            base_sections=base_sections,
            base_domains=base_domains,
            base_source_path=base_doc['source_file_path'] or '',
            patient_id=ev.patient_id,
            date_from=ev.date_from,
            date_to=ev.date_to,
        )

    @step
    async def generate_summary_step(
        self, ctx: Context, ev: SourcesEvent
    ) -> Union[TokenizedDocEvent, StopEvent]:
        base_document = dict(ev.base_sections)
        base_document[DOMAINS_KEY] = ev.base_domains
        prompt = GENERATE_SUMMARY_PROMPT.format(
            base_document_json=json.dumps(base_document, ensure_ascii=False, indent=2),
            date_from=ev.date_from,
            date_to=ev.date_to,
            sessions_text=ev.sessions_text,
            goals_text=ev.goals_text or 'אין מטרות',
        )
        try:
            logger.info(
                "Generating summary for patient=%s range=%s to %s",
                ev.patient_id, ev.date_from, ev.date_to,
            )
            response = await self._llm.acomplete(prompt)
            raw = (response.text or '').strip()
        except Exception as e:
            logger.error("Summary generation error: %s", e)
            return StopEvent(result=ERROR_MESSAGE)

        try:
            sections, domains = parse_generated_sections(raw)
        except GenerationParseError as e:
            logger.error(
                "Summary generation parse failure (%s). Raw LLM output:\n%s", e, raw
            )
            return StopEvent(result=PARSE_FAILURE_MESSAGE)

        return TokenizedDocEvent(
            sections=sections,
            domains=domains,
            base_source_path=ev.base_source_path,
            patient_id=ev.patient_id,
            date_to=ev.date_to,
        )

    @step
    async def build_doc_step(
        self, ctx: Context, ev: TokenizedDocEvent
    ) -> Union[ReidentifiedDocEvent, StopEvent]:
        try:
            reid_map = load_reid_map(self._reid_map_path)
        except Exception:
            return StopEvent(result="לא ניתן לייצר סיכום — מפת הזיהוי חסרה")

        reidentified_sections = {k: reidentify_text(reid_map, v) for k, v in ev.sections.items()}
        reidentified_domains = {k: reidentify_text(reid_map, v) for k, v in ev.domains.items()}

        metadata = db.get_patient_metadata(self._db_path, ev.patient_id)
        patient_token = f"PERSON_{ev.patient_id}"
        patient_name = reverse_lookup(reid_map, patient_token) or ev.patient_id
        national_id = metadata.get('national_id', '')
        date_of_birth = metadata.get('date_of_birth', '')

        return ReidentifiedDocEvent(
            sections=reidentified_sections,
            domains=reidentified_domains,
            tokenized_sections=ev.sections,
            tokenized_domains=ev.domains,
            base_source_path=ev.base_source_path,
            patient_id=ev.patient_id,
            date_to=ev.date_to,
            patient_name=patient_name,
            national_id=national_id,
            date_of_birth=date_of_birth,
        )

    @step
    async def save_doc_step(self, ctx: Context, ev: ReidentifiedDocEvent) -> StopEvent:
        reid_map = load_reid_map(self._reid_map_path)
        patient_folder_name = reverse_lookup(reid_map, f"PERSON_{ev.patient_id}") or ev.patient_id
        patient_folder = os.path.join(self._config.patients_root, patient_folder_name)

        existing = [f for f in os.listdir(patient_folder) if f.startswith(GENERATED_FILE_PREFIX)]
        next_num = len(existing) + 1
        docx_path = os.path.join(
            patient_folder, f"{GENERATED_FILE_PREFIX} {next_num} {patient_folder_name}.docx"
        )

        # Skeleton: the base document's physical file; fallback — the clean template
        doc = None
        if ev.base_source_path and os.path.isfile(ev.base_source_path):
            try:
                doc = build_summary_docx(
                    ev.base_source_path, ev.sections, ev.domains, header_date=ev.date_to
                )
            except Exception as e:
                logger.error(
                    "Skeleton build from base document failed (%s), falling back to template: %s",
                    ev.base_source_path, e,
                )
        if doc is None:
            header_fields = {
                'name': ev.patient_name,
                'national_id': ev.national_id,
                'date_of_birth': ev.date_of_birth,
            }
            try:
                doc = build_summary_docx(
                    FALLBACK_TEMPLATE_PATH, ev.sections, ev.domains,
                    header_date=ev.date_to, header_fields=header_fields,
                )
            except Exception as e:
                logger.error("Skeleton build from template failed: %s", e)
                return StopEvent(result=f"שגיאה בבניית הקובץ: {e}")

        # Single serialization — the stored hash matches the exact bytes on disk
        buf = io.BytesIO()
        doc.save(buf)
        file_bytes = buf.getvalue()
        try:
            with open(docx_path, 'wb') as fh:
                fh.write(file_bytes)
        except Exception as e:
            logger.error("Failed to save summary doc: %s", e)
            return StopEvent(result=f"שגיאה בשמירת הקובץ: {e}")
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # Write de-identified (tokenized) sections + domains to SQLite — never the
        # re-identified text; session_date = date_to (last session in range)
        for section_key, text in ev.tokenized_sections.items():
            chunk = FamilyAChunk(
                patient_id=ev.patient_id,
                template_type='clinic_visit_summary',
                session_date=ev.date_to,
                section=section_key,
                text_deidentified=text,
                source_file_path=docx_path,
            )
            db.insert_family_a_chunk(self._db_path, chunk)

        for domain_name, text in ev.tokenized_domains.items():
            db.insert_domain_finding(self._db_path, DomainFinding(
                patient_id=ev.patient_id,
                session_date=ev.date_to,
                domain_name=domain_name,
                domain_text_deidentified=text,
                parent_domain=PARENT_DOMAINS.get(domain_name),
            ))

        db.mark_file_ingested(self._db_path, docx_path, file_hash)

        logger.info("Summary generated and saved: %s", docx_path)
        return StopEvent(result=json.dumps({"docx": docx_path}))


async def run_generate_summary(
    patient_id: str,
    date_from: str,
    date_to: str,
    config: AppConfig,
    db_path: str,
    reid_map_path: str,
) -> str:
    workflow = GenerateSummaryWorkflow(
        config=config,
        db_path=db_path,
        reid_map_path=reid_map_path,
        timeout=120,
    )
    result = await workflow.run(
        patient_id=patient_id,
        date_from=date_from,
        date_to=date_to,
    )
    return result
