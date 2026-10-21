"""Q&A tab wiring (PLAN.md T6 items 31, 32): Enter submits, indicator + lock while answering."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import gradio as gr
import pytest

import app.generation.qa as qa
from app.storage.models import QueryParams
from app.ui.gradio_app import THINKING_MESSAGE, build_app, lock_question, unlock_question


@pytest.fixture
def ui(tmp_path, db_path):
    config = SimpleNamespace(patients_roots=[str(tmp_path)])
    return build_app(config, db_path, None, str(tmp_path / "reid_map.json"))


def _component(app, cls, label=None, value=None):
    matches = [b for b in app.blocks.values() if isinstance(b, cls)
               and (label is None or b.label == label) and (value is None or b.value == value)]
    assert len(matches) == 1
    return matches[0]


def _chain(app, block, event):
    """The functions run for *block*.*event*, following .then() links, in order."""
    fns = list(app.fns.values())
    first = next(f for f in fns if (block._id, event) in f.targets)
    chain = [first]
    while nxt := next((f for f in fns if f.trigger_after == chain[-1]._id), None):
        chain.append(nxt)
    return chain


def test_question_box_is_single_line_so_enter_submits(ui):
    box = _component(ui, gr.Textbox, label="שאלה")
    assert box.lines == 1 and box.max_lines > 1


@pytest.mark.parametrize("trigger", ["button", "enter"])
def test_send_shows_indicator_locks_then_unlocks(ui, trigger):
    box = _component(ui, gr.Textbox, label="שאלה")
    button = _component(ui, gr.Button, value="שלח")
    block, event = (button, "click") if trigger == "button" else (box, "submit")
    chain = _chain(ui, block, event)
    assert [f.name for f in chain] == ["lock_question", "ask_question", "unlock_question"]
    assert chain[0].queue is False  # indicator appears before the request is queued
    assert {o._id for o in chain[0].outputs} >= {box._id, button._id}
    assert {o._id for o in chain[2].outputs} == {box._id, button._id}


def test_lock_and_unlock_values():
    message, box, button = lock_question()
    assert message == THINKING_MESSAGE
    assert box["interactive"] is False and button["interactive"] is False
    assert all(u["interactive"] is True for u in unlock_question())


def test_sync_query_calls_run_off_the_event_loop_thread(monkeypatch):
    threads = []

    def fake_extract(question, patient_id, config):
        threads.append(threading.current_thread())
        return QueryParams(patient_id=patient_id, intent="summarize")

    def fake_retrieve(decision, config, db_path, pinecone_index):
        threads.append(threading.current_thread())
        return "no results"

    monkeypatch.setattr(qa, "extract_query_params", fake_extract)
    monkeypatch.setattr(qa, "retrieve", fake_retrieve)
    config = SimpleNamespace(anthropic=SimpleNamespace(
        generation_model="m", temperature_generation=0.0, max_tokens_generation=10,
    ), anthropic_api_key="test-key")
    answer = qa.asyncio.run(qa.run_query("שאלה", "p", config, "", MagicMock(), ""))
    assert answer == "no results"
    assert len(threads) == 2 and threading.main_thread() not in threads
