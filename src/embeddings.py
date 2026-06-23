# src/embeddings.py
from typing import ClassVar, List
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.bridge.pydantic import PrivateAttr
import cohere


class CohereEmbedder(BaseEmbedding):
    MODEL: ClassVar[str] = "embed-multilingual-v3.0"
    BATCH_SIZE: ClassVar[int] = 96

    _client: cohere.Client = PrivateAttr()

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self._client = cohere.Client(api_key)

    @classmethod
    def class_name(cls) -> str:
        return "CohereEmbedder"

    def _get_text_embedding(self, text: str) -> List[float]:
        resp = self._client.embed(texts=[text], model=self.MODEL, input_type="search_document")
        return resp.embeddings[0]

    def _get_query_embedding(self, text: str) -> List[float]:
        """Used at query time — input_type='search_query' for better retrieval."""
        resp = self._client.embed(texts=[text], model=self.MODEL, input_type="search_query")
        return resp.embeddings[0]

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return self._get_text_embedding(text)

    async def _aget_query_embedding(self, text: str) -> List[float]:
        return self._get_query_embedding(text)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Batch embedding used during indexing."""
        results = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            resp = self._client.embed(texts=batch, model=self.MODEL, input_type="search_document")
            results.extend(resp.embeddings)
        return results
