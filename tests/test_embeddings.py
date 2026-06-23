# test_embeddings.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock, patch
from src.embeddings import CohereEmbedder, VectorStore

def test_batching():
    with patch("src.embeddings.cohere.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Each embed call returns a response with embeddings
        def fake_embed(**kwargs):
            n = len(kwargs["texts"])
            resp = MagicMock()
            resp.embeddings = [[0.1] * 10 for _ in range(n)]
            return resp
        mock_client.embed.side_effect = fake_embed

        embedder = CohereEmbedder("fake-key")
        texts = [f"text {i}" for i in range(200)]
        vectors = embedder.embed(texts)

        call_count = mock_client.embed.call_count
        assert call_count == 3, f"Expected 3 batches, got {call_count}"
        assert len(vectors) == 200, f"Expected 200 vectors, got {len(vectors)}"
        print(f"Batching test passed: {call_count} batches, {len(vectors)} vectors")

def test_save_load():
    class FakeChunk:
        def __init__(self, text, metadata):
            self.text = text
            self.metadata = metadata

    chunks = [
        FakeChunk("Hello world", {"client_name": "Test", "section_title": "רקע:"}),
        FakeChunk("Another text", {"client_name": "Test2", "section_title": "הערות:"}),
    ]
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        tmp_path = f.name

    try:
        store = VectorStore()
        store.save(chunks, vectors, tmp_path)
        records = store.load(tmp_path)

        assert len(records) == 2
        assert records[0]["text"] == "Hello world"
        assert records[0]["metadata"]["client_name"] == "Test"
        assert records[0]["vector"] == [0.1, 0.2, 0.3]
        assert records[1]["text"] == "Another text"
        assert records[1]["metadata"]["section_title"] == "הערות:"
        print(f"Save/load test passed: {len(records)} records preserved correctly")
    finally:
        os.unlink(tmp_path)

if __name__ == "__main__":
    test_batching()
    test_save_load()
    print("All tests passed!")
