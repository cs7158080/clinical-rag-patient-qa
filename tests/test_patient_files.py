"""Patient-files tab listing (PLAN.md item 30): disk listing, not the DB."""
import ctypes
import sys

import pytest

from app.ui.patient_files import (
    list_patient_files, FOLDER_NOT_FOUND_MESSAGE, NO_FILES_MESSAGE,
)
from tests.fixtures.docx_factory import write_docx
from tests.fixtures.pdf_factory import write_pdf

NAME = "דנה כהן"


def _patient(root):
    folder = root / NAME
    folder.mkdir(parents=True)
    return folder


def test_lists_docx_and_pdf(tmp_path):
    folder = _patient(tmp_path)
    write_docx(folder / "אבחון.docx", ["טקסט"])
    write_pdf(folder / "סריקה.pdf")
    files, status = list_patient_files([str(tmp_path)], NAME)
    assert files == [
        ("אבחון.docx", str(folder / "אבחון.docx")),
        ("סריקה.pdf", str(folder / "סריקה.pdf")),
    ]
    assert status == ""


def test_pdf_fixture_is_a_pdf(tmp_path):
    with open(write_pdf(tmp_path / "x.pdf"), "rb") as f:
        data = f.read()
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")


def test_root_files_first_then_subfolders_with_relative_label(tmp_path):
    folder = _patient(tmp_path)
    write_docx(folder / "ב.docx", ["x"])
    (folder / "ארכיון").mkdir()
    write_pdf(folder / "ארכיון" / "א.pdf")
    files, _ = list_patient_files([str(tmp_path)], NAME)
    assert [label for label, _ in files] == ["ב.docx", "ארכיון/א.pdf"]
    assert files[1][1] == str(folder / "ארכיון" / "א.pdf")


def test_word_lock_files_skipped(tmp_path):
    folder = _patient(tmp_path)
    write_docx(folder / "אבחון.docx", ["x"])
    (folder / "~$אבחון.docx").write_bytes(b"lock")
    files, _ = list_patient_files([str(tmp_path)], NAME)
    assert [label for label, _ in files] == ["אבחון.docx"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes")
def test_hidden_and_system_entries_skipped(tmp_path):
    folder = _patient(tmp_path)
    write_docx(folder / "אבחון.docx", ["x"])
    (folder / "Thumbs.db").write_bytes(b"x")
    (folder / "נסתרת").mkdir()
    write_pdf(folder / "נסתרת" / "a.pdf")
    FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x2, 0x4
    ctypes.windll.kernel32.SetFileAttributesW(str(folder / "Thumbs.db"), FILE_ATTRIBUTE_SYSTEM)
    ctypes.windll.kernel32.SetFileAttributesW(str(folder / "נסתרת"), FILE_ATTRIBUTE_HIDDEN)
    files, _ = list_patient_files([str(tmp_path)], NAME)
    assert [label for label, _ in files] == ["אבחון.docx"]


def test_patient_found_in_second_root(tmp_path):
    root1, root2 = tmp_path / "r1", tmp_path / "r2"
    root1.mkdir()
    write_pdf(_patient(root2) / "a.pdf")
    files, status = list_patient_files([str(root1), str(root2)], NAME)
    assert [label for label, _ in files] == ["a.pdf"] and status == ""


def test_missing_folder_reports(tmp_path):
    files, status = list_patient_files([str(tmp_path)], NAME)
    assert files == [] and status == FOLDER_NOT_FOUND_MESSAGE.format(name=NAME)


def test_empty_display_name_never_lists_the_root(tmp_path):
    write_pdf(tmp_path / "a.pdf")
    files, status = list_patient_files([str(tmp_path)], "")
    assert files == [] and status == FOLDER_NOT_FOUND_MESSAGE.format(name="")


def test_empty_folder_reports(tmp_path):
    _patient(tmp_path)
    files, status = list_patient_files([str(tmp_path)], NAME)
    assert files == [] and status == NO_FILES_MESSAGE
