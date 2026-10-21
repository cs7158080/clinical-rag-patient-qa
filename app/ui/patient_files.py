"""Patient-files tab: list every file in the patient's folder on disk (PLAN.md item 30)."""
import os
import stat

from app.generation.summary_generator import find_patient_folder

LOCK_FILE_PREFIX = "~$"
_HIDDEN_OR_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM

FOLDER_NOT_FOUND_MESSAGE = "תיקיית המטופל לא נמצאה: {name}"
NO_FILES_MESSAGE = "אין קבצים בתיקיית המטופל"
READ_ERROR_MESSAGE = "שגיאה בקריאת תיקיית המטופל: {error}"


def _is_skipped(entry: os.DirEntry) -> bool:
    """Word lock files (~$) and Windows hidden/system entries (Thumbs.db, desktop.ini)."""
    if entry.name.startswith(LOCK_FILE_PREFIX):
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & _HIDDEN_OR_SYSTEM)


def _list_folder(folder: str, rel_dir: str) -> list[tuple[str, str]]:
    """Files directly in *folder* first, then each subfolder (recursively); A-Z within each."""
    files, subdirs = [], []
    with os.scandir(folder) as it:
        for entry in it:
            if _is_skipped(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                subdirs.append(entry)
            elif entry.is_file():
                files.append(entry)
    result = [(rel_dir + e.name, e.path) for e in sorted(files, key=lambda e: e.name)]
    for d in sorted(subdirs, key=lambda e: e.name):
        result.extend(_list_folder(d.path, rel_dir + d.name + "/"))
    return result


def list_patient_files(patients_roots: list, display_name: str) -> tuple[list[tuple[str, str]], str]:
    """Return ([(label, absolute_path)], status) for <root>/<display_name>.

    label is the path relative to the patient folder ('/'-separated). status is ''
    when files were found, otherwise a Hebrew message for the UI (W2).
    """
    folder = find_patient_folder(patients_roots, display_name) if display_name else None
    if folder is None:
        return [], FOLDER_NOT_FOUND_MESSAGE.format(name=display_name)
    try:
        files = _list_folder(folder, "")
    except OSError as e:
        return [], READ_ERROR_MESSAGE.format(error=e)
    return files, "" if files else NO_FILES_MESSAGE
