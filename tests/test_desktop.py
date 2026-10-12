"""test_desktop.py — desktop launcher helpers (no window, no server, no API)."""

import socket
import sys
import uuid
from types import SimpleNamespace

import pytest

from app import desktop


def _config(tmp_path, run_mode="production", with_model=True):
    models = tmp_path / "models"
    if with_model:
        (models / "heBERT_NER_onnx").mkdir(parents=True)
        (models / "heBERT_NER_onnx" / "model.onnx").write_bytes(b"")
    return SimpleNamespace(models_dir=str(models), run_mode=run_mode)


def test_no_warnings_when_all_ok(tmp_path):
    assert desktop.startup_warnings(_config(tmp_path), pinecone_ok=True) == []


def test_pinecone_down_warns(tmp_path):
    assert desktop.startup_warnings(_config(tmp_path), pinecone_ok=False) == [
        desktop.MSG_PINECONE_UNAVAILABLE
    ]


def test_missing_ner_warns_in_production_only(tmp_path):
    prod = _config(tmp_path / "p", with_model=False)
    test = _config(tmp_path / "t", run_mode="test", with_model=False)
    assert desktop.startup_warnings(prod, pinecone_ok=True) == [desktop.MSG_NER_MISSING]
    assert desktop.startup_warnings(test, pinecone_ok=True) == []


def test_missing_env_file_is_a_startup_error(tmp_path):
    with pytest.raises(desktop.StartupError) as exc:
        desktop.load_config(root_dir=str(tmp_path))
    assert str(exc.value) == desktop.MSG_ENV_FILE_MISSING


def test_port_in_use_detects_listener():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert desktop.port_in_use("127.0.0.1", port)
    assert not desktop.port_in_use("127.0.0.1", port)


def test_redirect_std_streams_when_none(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "desktop.log"
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    desktop.redirect_std_streams(str(log))
    print("hello")
    sys.stdout.flush()
    assert "hello" in log.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_second_lock_reports_already_running():
    name = f"Local\\ClinicalRAG.Test.{uuid.uuid4()}"
    first, first_running = desktop.acquire_single_instance_lock(name)
    second, second_running = desktop.acquire_single_instance_lock(name)
    assert first and not first_running
    assert second_running
