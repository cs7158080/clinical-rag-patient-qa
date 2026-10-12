"""
test_build_release.py — release ZIP build + verification (scripts/build_release.py).

Builds from a throw-away git repo with a tiny fake model, so no real model,
network or origin remote is needed.
"""

import os
import subprocess
import zipfile

import pytest

from scripts.build_release import (
    MODEL_SUBDIR, REQUIRED, TOP, ReleaseError, build, forbidden_reason, verify_zip,
)


def _make_zip(path, rels):
    with zipfile.ZipFile(path, "w") as zf:
        for rel in rels:
            zf.writestr(f"{TOP}/{rel}", "x")
    return str(path)


# ---------------------------------------------------------------------------
# forbidden_reason / verify_zip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    ".env", "app/.env", ".env.local", "data/clinical_rag.db", "data/reid_map.json",
    "logs/desktop.log", ".venv/Scripts/python.exe", ".claude/plans/real-data/PLAN.md",
    ".git/config", "backup/old.db",
])
def test_forbidden(rel):
    assert forbidden_reason(rel)


@pytest.mark.parametrize("rel", [
    ".env.example", "data/.gitkeep", "logs/.gitkeep", "app/desktop.py",
    f"models/{MODEL_SUBDIR}/model.onnx",
])
def test_allowed(rel):
    assert forbidden_reason(rel) is None


def test_verify_complete_zip_passes(tmp_path):
    assert verify_zip(_make_zip(tmp_path / "r.zip", REQUIRED + ["data/.gitkeep"])) == []


def test_verify_reports_missing_model(tmp_path):
    rels = [r for r in REQUIRED if not r.endswith("model.onnx")]
    errors = verify_zip(_make_zip(tmp_path / "r.zip", rels))
    assert errors == [f"missing: models/{MODEL_SUBDIR}/model.onnx"]


def test_verify_reports_env_and_patient_data(tmp_path):
    errors = verify_zip(_make_zip(tmp_path / "r.zip", REQUIRED + [".env", "data/reid_map.json"]))
    assert any(e.startswith("forbidden: .env") for e in errors)
    assert any(e.startswith("forbidden: data/reid_map.json") for e in errors)


def test_verify_reports_files_outside_top_folder(tmp_path):
    path = _make_zip(tmp_path / "r.zip", REQUIRED)
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("stray.txt", "x")
    assert verify_zip(path) == [f"outside {TOP}/: stray.txt"]


# ---------------------------------------------------------------------------
# build — end to end against a temporary git repo
# ---------------------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Git repo holding every required code file; fake model in models/ (untracked)."""
    root = tmp_path / "repo"
    for rel in REQUIRED:
        if rel != "VERSION" and not rel.startswith("models/"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
    (root / "data").mkdir()
    (root / "data" / ".gitkeep").write_text("")
    model = root / "models" / MODEL_SUBDIR
    model.mkdir(parents=True)
    for rel in REQUIRED:
        if rel.startswith("models/"):
            (root / rel).write_text("m")
    (root / ".gitignore").write_text(".env\nmodels/\ndata/*\n!data/.gitkeep\n")
    (root / ".env").write_text("ANTHROPIC_API_KEY=secret")
    (root / "data" / "clinical_rag.db").write_text("phi")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    return root


def test_build_ships_code_and_model_but_no_secrets(repo):
    zip_path = build("HEAD", "models", "dist", fetch=False)
    assert os.path.basename(zip_path).startswith(f"{TOP}-") and zip_path.endswith(".zip")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        version = zf.read(f"{TOP}/VERSION").decode()
    assert f"{TOP}/models/{MODEL_SUBDIR}/model.onnx" in names
    assert f"{TOP}/.env" not in names
    assert f"{TOP}/data/clinical_rag.db" not in names
    assert "ref=HEAD" in version


def test_build_fails_without_model(repo):
    with pytest.raises(ReleaseError, match="NER model not found"):
        build("HEAD", "no_such_models", "dist", fetch=False)
    assert not (repo / "dist").exists()


def test_build_deletes_zip_that_fails_verification(repo):
    _git(repo, "add", "-f", ".env")      # someone force-committed the keys file
    _git(repo, "commit", "-q", "-m", "oops")
    with pytest.raises(ReleaseError, match="forbidden: .env"):
        build("HEAD", "models", "dist", fetch=False)
    assert os.listdir(repo / "dist") == []
