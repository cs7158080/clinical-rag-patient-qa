"""
result.py — outcome of ingesting one file (PLAN.md item 24c) and the UI load report.
"""

import os
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["ok", "skipped", "ignored", "blocked", "error"]


@dataclass
class IngestResult:
    status: Status
    message: str = ""                                    # Hebrew; empty for ok / skipped / ignored
    warnings: list[str] = field(default_factory=list)    # Hebrew; one ⚠ line each in the report
    counts: dict[str, int] = field(default_factory=dict) # dry run only: sections/domains/goals/sessions


def format_report(results: list[tuple[str, IngestResult]]) -> str:
    """Render the load report: summary line, then one line per file (+ indented ⚠ lines)."""
    counts = {s: 0 for s in ("ok", "skipped", "ignored", "blocked", "error")}
    n_warnings = 0
    lines: list[str] = []
    for file_path, r in results:
        counts[r.status] += 1
        n_warnings += len(r.warnings)
        name = os.path.basename(file_path)
        if r.status == "ok":
            lines.append(f"✓  {name}")
        elif r.status == "skipped":
            lines.append(f"○  {name} (ללא שינוי)")
        elif r.status == "ignored":
            lines.append(f"○  {name} (דולג — לא מסמך קליני)")
        elif r.status == "blocked":
            lines.append(f"✗  {name} — חסום: {r.message}")
        else:
            lines.append(f"✗  {name} — שגיאה: {r.message}")
        lines.extend(f"      ⚠  {w}" for w in r.warnings)

    summary = (
        f"סיום טעינה — {counts['ok']} נטענו, {counts['skipped']} ללא שינוי, "
        f"{counts['blocked']} חסומים, {counts['error']} שגיאות, "
        f"{counts['ignored']} דולגו (לא מסמך קליני), {n_warnings} אזהרות"
    )
    return summary + "\n\n" + "\n".join(lines)
