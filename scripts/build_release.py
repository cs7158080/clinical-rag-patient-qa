"""
build_release.py — build the client install ZIP (committed code + the NER model).

Exports the committed code of a git ref (default origin/main, after `git fetch`) with
`git archive`, adds models/ (not in git) and a VERSION file, all under one top folder
ClinicalQA/, then re-opens the ZIP and verifies it: required files present, nothing
secret or patient-related inside (.env, data/, logs/, .venv/, ...). A ZIP that fails
verification is deleted, so a bad release never stays on disk.

Run from the main project folder (the one that has models/):
    uv run python scripts/build_release.py [--ref origin/main] [--models-dir models]
                                           [--out-dir dist] [--no-fetch]
Output: dist/ClinicalQA-<YYYY-MM-DD>-<shortsha>.zip
"""

import argparse
import datetime
import os
import subprocess
import sys
import zipfile

TOP = "ClinicalQA"
MODEL_SUBDIR = "heBERT_NER_onnx"

# Paths relative to ClinicalQA/ that every release must contain.
REQUIRED = [
    "VERSION",
    "setup.bat",
    "pyproject.toml",
    "uv.lock",
    ".env.example",
    "config.yaml",
    "app/desktop.py",
    f"models/{MODEL_SUBDIR}/model.onnx",
    f"models/{MODEL_SUBDIR}/config.json",
    f"models/{MODEL_SUBDIR}/tokenizer.json",
    f"models/{MODEL_SUBDIR}/tokenizer_config.json",
    f"models/{MODEL_SUBDIR}/special_tokens_map.json",
    f"models/{MODEL_SUBDIR}/vocab.txt",
]


class ReleaseError(Exception):
    """Build or verification failed; the message says what to fix."""


def forbidden_reason(rel: str):
    """Why *rel* (a '/'-separated path inside ClinicalQA/) must not ship, or None if it may."""
    parts = rel.split("/")
    name = parts[-1]
    if name.startswith(".env") and name != ".env.example":
        return "API keys file"
    if parts[0] in (".venv", ".claude", ".git"):
        return f"{parts[0]}/ folder"
    if parts[0] in ("data", "logs") and name != ".gitkeep":
        return f"{parts[0]}/ content (patient data / logs)"
    if name.endswith(".db") or name == "reid_map.json":
        return "database / re-id map"
    return None


def verify_zip(zip_path: str) -> list:
    """Problems found in the release ZIP (empty list = OK to ship)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    errors = [f"outside {TOP}/: {n}" for n in names if not n.startswith(TOP + "/")]
    rels = {n[len(TOP) + 1:] for n in names if n.startswith(TOP + "/")}
    errors += [f"missing: {req}" for req in REQUIRED if req not in rels]
    for rel in sorted(rels):
        reason = forbidden_reason(rel)
        if reason:
            errors.append(f"forbidden: {rel} ({reason})")
    return errors


def _git(*args) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def build(ref: str, models_dir: str, out_dir: str, fetch: bool = True) -> str:
    """Build and verify the release ZIP; return its path. Raises ReleaseError."""
    model_src = os.path.join(models_dir, MODEL_SUBDIR)
    if not os.path.isfile(os.path.join(model_src, "model.onnx")):
        raise ReleaseError(
            f"NER model not found in {model_src} — run from the main project folder "
            "or pass --models-dir."
        )
    if fetch:
        _git("fetch", "origin")
    sha = _git("rev-parse", "--short", ref)
    today = datetime.date.today().isoformat()

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{TOP}-{today}-{sha}.zip")
    _git("archive", "--format=zip", f"--prefix={TOP}/", "-o", zip_path, ref)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(os.listdir(model_src)):
                path = os.path.join(model_src, name)
                if os.path.isfile(path):
                    zf.write(path, f"{TOP}/models/{MODEL_SUBDIR}/{name}")
            zf.writestr(f"{TOP}/VERSION", f"commit={sha}\ndate={today}\nref={ref}\n")
        errors = verify_zip(zip_path)
    except BaseException:
        os.remove(zip_path)
        raise
    if errors:
        os.remove(zip_path)
        raise ReleaseError("release ZIP failed verification (deleted):\n  " + "\n  ".join(errors))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the client install ZIP.")
    parser.add_argument("--ref", default="origin/main", help="git ref to package")
    parser.add_argument("--models-dir", default="models", help="folder that holds heBERT_NER_onnx/")
    parser.add_argument("--out-dir", default="dist")
    parser.add_argument("--no-fetch", action="store_true", help="skip `git fetch` (offline trial)")
    args = parser.parse_args()
    try:
        zip_path = build(args.ref, args.models_dir, args.out_dir, fetch=not args.no_fetch)
    except ReleaseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"Release built and verified: {zip_path} ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
