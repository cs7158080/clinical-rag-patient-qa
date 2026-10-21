"""Safe store order (PLAN.md item 24a, T2 part 6): any failure keeps the previous data."""

import asyncio
import logging
import sqlite3
from types import SimpleNamespace

import pytest

from app.ingestion import pipeline
from app.storage import db
from tests.fixtures.docx_factory import write_docx

_PATIENT = "ישראל ישראלי"
_STORE_FAILED = "שמירת הנתונים נכשלה — הנתונים הקודמים נשמרו. נסי לטעון שוב"


def _boom(*args, **kwargs):
    raise sqlite3.OperationalError("disk full")


@pytest.fixture
def env(tmp_path, db_path, monkeypatch):
    """Real ingestion into *db_path*; NER, Cohere and Pinecone replaced by stubs."""
    from app.deidentification import validation

    monkeypatch.setattr(pipeline, "load_ner_model", lambda models_dir: None)
    monkeypatch.setattr(validation, "_check_ner_rescan", lambda text: True)
    monkeypatch.setattr(pipeline, "get_cohere_embedding", lambda text, key, model: [0.0])
    index = SimpleNamespace(upserted=[], deleted=[])
    monkeypatch.setattr(pipeline, "upsert_vectors",
                        lambda idx, vectors: idx.upserted.extend(v["id"] for v in vectors))
    monkeypatch.setattr(pipeline, "delete_vectors", lambda idx, ids: idx.deleted.extend(ids))
    folder = tmp_path / "patients" / _PATIENT
    folder.mkdir(parents=True)
    config = SimpleNamespace(models_dir="", run_mode="test", data_dir=str(tmp_path),
                             cohere_api_key="", cohere=SimpleNamespace(model=""))
    reid_map: dict = {}                 # shared, so the patient id is stable across loads

    def ingest():
        [(_, result)] = asyncio.run(pipeline.run_ingestion(
            [str(tmp_path / "patients")], config, reid_map, db_path, index))
        return result

    return SimpleNamespace(folder=folder, index=index, ingest=ingest, db_path=db_path)


def _write_plan(folder, session_dates):
    body = [line for d in session_dates for line in (d, f"מפגש {d}.")]
    write_docx(folder / f"תוכנית טיפול {_PATIENT}.docx", ['בס"ד', f"שם: {_PATIENT}", *body])


def _session_dates(db_path):
    with sqlite3.connect(db_path) as conn:
        return [r[0] for r in conn.execute(
            "SELECT session_date FROM treatment_sessions ORDER BY session_date")]


def test_transaction_rolls_back_everything_on_error(db_path):              # 6-ב
    with pytest.raises(RuntimeError):
        with db.transaction(db_path) as conn:
            db.upsert_patient_metadata(db_path, "p1", "display_name", "x", conn=conn)
            raise RuntimeError("fail")
    assert db.get_patient_metadata(db_path, "p1") == {}


def test_failed_plan_store_keeps_previous_data_and_is_retried(env, monkeypatch):  # 24a, 6-ג
    _write_plan(env.folder, ["01/03/2026", "08/03/2026"])
    assert env.ingest().status == "ok"

    _write_plan(env.folder, ["01/03/2026", "15/03/2026"])
    real_insert = pipeline.insert_treatment_session
    monkeypatch.setattr(pipeline, "insert_treatment_session", _boom)
    result = env.ingest()

    assert (result.status, result.message) == ("error", _STORE_FAILED)
    assert _session_dates(env.db_path) == ["2026-03-01", "2026-03-08"]
    assert env.index.deleted == []                      # nothing pruned

    # The hash was not recorded → the next load tries again, then prunes 08/03.
    monkeypatch.setattr(pipeline, "insert_treatment_session", real_insert)
    assert env.ingest().status == "ok"
    assert _session_dates(env.db_path) == ["2026-03-01", "2026-03-15"]
    assert len(env.index.deleted) == 1
    assert env.index.deleted[0].endswith("_session_2026-03-08")


def test_failed_diagnosis_store_keeps_previous_data(env, monkeypatch):    # 6-א
    path = env.folder / f"סיכום אבחון {_PATIENT}.docx"
    header = ['בס"ד', "01/03/2026", "הנדון: סיכום אבחון", f"שם: {_PATIENT}", "רקע:"]
    write_docx(path, [*header, "ילד בן שש."])
    assert env.ingest().status == "ok"

    write_docx(path, [*header, "ילד בן שבע."])
    monkeypatch.setattr(pipeline, "mark_file_ingested", _boom)       # fails at the very end
    result = env.ingest()

    assert (result.status, result.message) == ("error", _STORE_FAILED)
    with sqlite3.connect(env.db_path) as conn:
        assert conn.execute(
            "SELECT text_deidentified FROM document_sections WHERE section = 'רקע'"
        ).fetchall() == [("ילד בן שש.",)]


def test_prune_failure_is_logged_not_reported(env, monkeypatch, caplog):   # 6-ד
    _write_plan(env.folder, ["01/03/2026", "08/03/2026"])
    assert env.ingest().status == "ok"

    _write_plan(env.folder, ["01/03/2026"])
    monkeypatch.setattr(pipeline, "delete_vectors", lambda idx, ids: _boom())
    with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
        result = env.ingest()

    assert (result.status, result.warnings) == ("ok", [])
    assert _session_dates(env.db_path) == ["2026-03-01"]
    assert "left in Pinecone" in caplog.text
