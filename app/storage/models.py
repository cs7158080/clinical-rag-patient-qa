from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FamilyAChunk:
    patient_id: str
    template_type: str
    session_date: str
    section: str
    text_deidentified: str
    source_file_path: Optional[str] = None


@dataclass
class TreatmentGoalsChunk:
    patient_id: str
    session_date: str
    goals_text_deidentified: str
    pinecone_id: Optional[str] = None
    source_file_path: Optional[str] = None


@dataclass
class TreatmentSessionChunk:
    patient_id: str
    session_date: str
    session_text_deidentified: str
    pinecone_id: Optional[str] = None
    source_file_path: Optional[str] = None


@dataclass
class DomainFinding:
    patient_id: str
    session_date: str
    domain_name: str
    domain_text_deidentified: str
    parent_domain: Optional[str] = None


@dataclass
class QueryParams:
    patient_id: str
    intent: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    template_type: Optional[str] = None
    topic: Optional[str] = None
    session_limit: Optional[int] = None


@dataclass
class RetrievalResult:
    chunks: list
    source_table: str
    count: int
