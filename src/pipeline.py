# src/pipeline.py
import os
from typing import List

from dotenv import load_dotenv
from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.llms.anthropic import Anthropic
from llama_index.vector_stores.pinecone import PineconeVectorStore
from pinecone import Pinecone

from src.embeddings import CohereEmbedder

MAX_CONTEXT_CHARS = 12_000

SYSTEM_PROMPT = (
    "אתה עוזר לקלינאית תקשורת לסקור קבצי לקוחות. "
    "ענה אך ורק על בסיס ההקשר המסופק. "
    "אל תשתמש בידע כלשהו מחוץ להקשר. אם התשובה אינה בהקשר, אמור זאת במפורש."
)


def format_docs_from_hits(hits: List[dict]) -> str:
    parts, budget = [], MAX_CONTEXT_CHARS
    for h in hits:
        entry = f"[{h.get('client_name', '')} — {h.get('section_title', '')}]\n{h['text']}"
        if budget - len(entry) < 0:
            parts.append(entry[:budget])
            break
        parts.append(entry)
        budget -= len(entry)
    return "\n\n---\n\n".join(parts)


class RAGPipeline:
    def __init__(self, index: VectorStoreIndex, embedder: CohereEmbedder, llm: Anthropic, top_n: int = 5):
        self._index = index
        self._embedder = embedder
        self._llm = llm
        self._top_n = top_n

    def reload(self):
        load_dotenv()
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        pc_index = pc.Index(os.environ["PINECONE_INDEX_NAME"])
        vector_store = PineconeVectorStore(pinecone_index=pc_index)
        self._index = VectorStoreIndex.from_vector_store(vector_store)

    def search(self, question: str, template_type: str = None) -> List[dict]:
        fetch_n = self._top_n * 3 if template_type else self._top_n
        filters = None
        if template_type:
            filters = MetadataFilters(filters=[
                MetadataFilter(key="template_type", value=template_type)
            ])

        retriever = VectorIndexRetriever(
            index=self._index,
            similarity_top_k=fetch_n,
            filters=filters,
        )
        nodes_with_scores = retriever.retrieve(question)

        hits = []
        for nws in nodes_with_scores:
            record = nws.node.metadata.copy()
            record["text"] = nws.node.get_content()
            record["similarity_score"] = round(nws.score or 0.0, 4)
            hits.append(record)

        # Post-filter as safety net (Pinecone filter is not always exact)
        if template_type:
            hits = [h for h in hits if h.get("template_type") == template_type]

        return hits[: self._top_n]

    def ask(self, question: str, hits: List[dict]) -> str:
        context = format_docs_from_hits(hits)
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=f"שאלה: {question}\n\nהקשר:\n{context}"),
        ]
        response = self._llm.chat(messages)
        return response.message.content


def build_pipeline(top_n: int = 5) -> RAGPipeline:
    load_dotenv()
    embedder = CohereEmbedder(os.environ["COHERE_API_KEY"])
    llm = Anthropic(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )

    Settings.embed_model = embedder
    Settings.llm = llm

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    pc_index = pc.Index(os.environ["PINECONE_INDEX_NAME"])
    vector_store = PineconeVectorStore(pinecone_index=pc_index)
    index = VectorStoreIndex.from_vector_store(vector_store)

    return RAGPipeline(index, embedder, llm, top_n)
