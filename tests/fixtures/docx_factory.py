"""
docx_factory.py — synthetic .docx builders for parser tests (PLAN.md section 7, T0 part 5).

Content is invented; structure mirrors the client's corpus. Files are written
to the caller's tmp_path — never committed, never read from the corpus.
"""

from docx import Document


def write_docx(path, paragraphs: list[str]):
    """Write one Word paragraph per item of *paragraphs* and return *path*.

    A '\\n' inside an item becomes a Shift+Enter line break (same paragraph),
    as therapists type it in Word.
    """
    doc = Document()
    for text in paragraphs:
        para = doc.add_paragraph()
        for i, line in enumerate(text.split("\n")):
            run = para.add_run(line)
            if i < len(text.split("\n")) - 1:
                run.add_break()
    doc.save(path)
    return path
