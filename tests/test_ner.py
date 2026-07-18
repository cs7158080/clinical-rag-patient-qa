"""
test_ner.py — sliding-window NER wrapper tests.

The ONNX model is never loaded: _extract_entities_window is monkeypatched
with a fake that finds a known name by string search, so these tests cover
window splitting, offset mapping back to full-text coordinates, and
overlap-zone deduplication only. All names are fictitious.
"""

import pytest

from app.deidentification import ner


_NAME = "יוסי כהן"


def _fake_window_fn(window_text: str) -> "list[dict]":
    """Return every full occurrence of _NAME with window-local offsets."""
    entities = []
    pos = window_text.find(_NAME)
    while pos != -1:
        entities.append(
            {"text": _NAME, "label": "PER", "start": pos, "end": pos + len(_NAME)}
        )
        pos = window_text.find(_NAME, pos + 1)
    return entities


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(ner, "_session", object())  # is_model_loaded() → True
    monkeypatch.setattr(ner, "_extract_entities_window", _fake_window_fn)


def _text_with_name_at(offset: int, total_len: int) -> str:
    """Build a filler text of *total_len* chars with _NAME planted at *offset*."""
    filler = "מילים בעברית לצורך רקע " * 400
    return filler[:offset] + _NAME + filler[: total_len - offset - len(_NAME)]


def test_short_text_single_window(fake_model):
    text = f"הגיע {_NAME} לקליניקה."
    entities = ner.extract_entities(text)
    assert len(entities) == 1
    assert text[entities[0]["start"]:entities[0]["end"]] == _NAME


def test_entity_beyond_first_window_found(fake_model):
    """A name far past _WINDOW_CHARS must be found with correct global offsets."""
    offset = ner._WINDOW_CHARS * 3 + 17
    text = _text_with_name_at(offset, total_len=ner._WINDOW_CHARS * 4)
    entities = ner.extract_entities(text)
    assert len(entities) == 1
    ent = entities[0]
    assert text[ent["start"]:ent["end"]] == _NAME


def test_entity_in_overlap_zone_deduped(fake_model):
    """A name inside an overlap zone is seen by two windows but returned once."""
    stride = ner._WINDOW_CHARS - ner._OVERLAP_CHARS
    offset = stride + 10  # inside window 1's overlap tail AND window 2's head
    text = _text_with_name_at(offset, total_len=ner._WINDOW_CHARS * 3)
    entities = ner.extract_entities(text)
    assert len(entities) == 1
    ent = entities[0]
    assert (ent["start"], ent["end"]) == (offset, offset + len(_NAME))


def test_entity_spanning_stride_boundary(fake_model):
    """A name that straddles the stride boundary is attributed to exactly one
    window and never cut."""
    stride = ner._WINDOW_CHARS - ner._OVERLAP_CHARS
    offset = stride - 3  # starts before the boundary, ends after it
    text = _text_with_name_at(offset, total_len=ner._WINDOW_CHARS * 3)
    entities = ner.extract_entities(text)
    assert len(entities) == 1
    ent = entities[0]
    assert text[ent["start"]:ent["end"]] == _NAME


def test_model_not_loaded_raises():
    with pytest.raises(RuntimeError):
        ner.extract_entities("טקסט כלשהו")
