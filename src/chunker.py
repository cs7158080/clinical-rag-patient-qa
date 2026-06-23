import hashlib
import os
import yaml
from dataclasses import dataclass
from typing import Any, List, Sequence

from docx import Document
from llama_index.core.bridge.pydantic import PrivateAttr
from llama_index.core.node_parser import NodeParser as BaseNodeParser
from llama_index.core.schema import BaseNode, TextNode

MAX_CHUNK_CHARS = 1500


@dataclass
class TemplateDefinition:
    id: str
    name: str
    required_headers: list
    section_headers: list


@dataclass
class Chunk:
    text: str
    metadata: dict

    def to_llama_node(self) -> TextNode:
        content_hash = hashlib.md5(self.text.encode()).hexdigest()[:8]
        node_id = hashlib.md5(
            (self.metadata["source"] + str(self.metadata["chunk_index"]) + content_hash).encode()
        ).hexdigest()
        return TextNode(id_=node_id, text=self.text, metadata=self.metadata)


class TemplateRegistry:
    def __init__(self, config_path: str = "templates_config.yaml"):
        raw = yaml.safe_load(open(config_path, encoding="utf-8"))
        self.templates = [TemplateDefinition(**t) for t in raw["templates"]]

    def detect(self, paragraphs: list) -> TemplateDefinition | None:
        """Return the first template whose required_headers all appear in the doc."""
        for tmpl in self.templates:
            if all(h in paragraphs for h in tmpl.required_headers):
                return tmpl
        return None  # no matching template — skip file


def _extract_field(paragraphs: list, label: str):
    """Extract value after a label like 'תאריך:' from the paragraph list."""
    for p in paragraphs:
        if p.startswith(label):
            return p[len(label):].strip() or "None"
    return "Noneupdated"


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list:
    """Split text into chunks of at most chunk_size chars with overlap."""
    if len(text) <= chunk_size:
        return [text]
    for sep in ["\n\n", "\n", ".", " "]:
        if sep not in text:
            continue
        parts = text.split(sep)
        chunks, buf = [], ""
        for part in parts:
            candidate = (buf + sep + part) if buf else part
            if len(candidate) <= chunk_size:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                tail = buf[-chunk_overlap:] if buf and chunk_overlap else ""
                buf = (tail + sep + part) if tail else part
        if buf:
            chunks.append(buf)
        result = [c.strip() for c in chunks if c.strip()]
        if result:
            return result
    # Fallback: hard character slice
    step = max(1, chunk_size - chunk_overlap)
    return [text[i:i + chunk_size].strip() for i in range(0, len(text), step) if text[i:i + chunk_size].strip()]


class Chunker:
    def __init__(self, registry: TemplateRegistry, max_chars: int = MAX_CHUNK_CHARS):
        self.registry = registry
        self.max_chars = max_chars

    def chunk_file(self, path: str) -> list:
        if not path.endswith(".docx"):
            return []

        doc = Document(path)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

        tmpl = self.registry.detect(paragraphs)
        if tmpl is None:
            return []  # not a recognized template — skip silently

        client_name = os.path.splitext(os.path.basename(path))[0]
        doc_date = _extract_field(paragraphs, "תאריך:")
        birth_date = _extract_field(paragraphs, "ת.ל.:")

        section_header_set = set(tmpl.section_headers)
        sections = []  # list of (title: str, paras: list[str])
        current_title, current_paras = "פתיח", []

        for para in paragraphs:
            if para in section_header_set:
                if current_paras:
                    sections.append((current_title, current_paras))
                current_title, current_paras = para, []
            else:
                current_paras.append(para)

        # Remaining paragraphs after the last known section header
        if current_paras:
            sections.append((current_title, current_paras))

        # Detect where the template ends and free text begins
        last_known_idx = max(
            (i for i, (t, _) in enumerate(sections) if t in section_header_set),
            default=-1,
        )

        chunks = []
        for sec_idx, (title, paras) in enumerate(sections):
            is_free = sec_idx > last_known_idx and title not in section_header_set
            body = "\n".join(paras)
            display_title = "הערות חופשיות" if is_free else title

            parts = [body] if len(body) <= self.max_chars else _split_text(body, self.max_chars, 100)

            for sub_idx, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                chunks.append(
                    Chunk(
                        text=part,
                        metadata={
                            "source": os.path.abspath(path),
                            "client_name": client_name,
                            "file_name": os.path.basename(path),
                            "template_type": tmpl.id,
                            "template_name": tmpl.name,
                            "section_title": display_title,
                            "document_date": doc_date,
                            "birth_date": birth_date,
                            "is_free_text": is_free,
                            "chunk_index": sec_idx * 100 + sub_idx,
                            "char_count": len(part),
                        },
                    )
                )
        return chunks


class DocxTemplateNodeParser(BaseNodeParser):
    _chunker: Any = PrivateAttr()

    def __init__(self, registry: TemplateRegistry, **kwargs):
        super().__init__(**kwargs)
        self._chunker = Chunker(registry)

    @classmethod
    def class_name(cls) -> str:
        return "DocxTemplateNodeParser"

    def _parse_nodes(
        self,
        nodes: Sequence[BaseNode],
        show_progress: bool = False,
        **kwargs: Any,
    ) -> List[BaseNode]:
        result = []
        for node in nodes:
            file_path = node.metadata.get("file_path", "")
            if not file_path:
                continue
            chunks = self._chunker.chunk_file(file_path)
            result.extend(c.to_llama_node() for c in chunks)
        return result
