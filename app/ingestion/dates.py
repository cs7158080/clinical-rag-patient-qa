"""
dates.py — shared date parser for ingestion (PLAN.md items 10, 18, 20a, 20b, 20c, 21a).

Two steps:
  1. parse_date(text)   — one written date → ParsedDate (the year may be missing).
  2. resolve_dates(...) — a document-ordered sequence → real dates + Hebrew warnings
                          (inferred year, future, before treatment start).

Callers decide WHICH text is a date (e.g. a line that is only a date). This module
never searches inside free text, so "1/2 כוס" or "28/11 –" inside a sentence is
never mistaken for a date (20c).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# D/M/YYYY, D/M/YY, D/M, D/M/   and   D.M.YYYY, D.M.YY
# The separator must be the same throughout. "No year" is accepted only with "/":
# "1.5" is far more likely a number than a date.
_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})(?P<sep>[/.])(?P<month>\d{1,2})"
    r"(?:(?P=sep)(?P<year>\d{4}|\d{2})?)?"
)


class DateError(ValueError):
    """A date-shaped text that cannot be a real date. str(exc) is a Hebrew message for the UI."""


@dataclass(frozen=True)
class ParsedDate:
    raw: str              # the text as written — used in messages so the client can find it
    day: int
    month: int
    year: Optional[int]   # None = written without a year (20b)


@dataclass
class ResolvedDates:
    dates: list[date]
    warnings: list[str] = field(default_factory=list)


def format_date(d: date) -> str:
    """dd/mm/yyyy — the way dates are written in the documents."""
    return d.strftime("%d/%m/%Y")


def parse_date(text: str) -> Optional[ParsedDate]:
    """Parse *text* (the whole string, stripped) as one written date.

    Returns None when the text is not shaped like a date.
    Raises DateError when it is shaped like a date but does not exist (31/11/2025).
    """
    raw = text.strip()
    m = _DATE_RE.fullmatch(raw)
    if not m or (m["sep"] == "." and m["year"] is None):
        return None
    day, month = int(m["day"]), int(m["month"])
    year: Optional[int] = None
    if m["year"] is not None:
        year = int(m["year"])
        if len(m["year"]) == 2:
            year += 2000                  # 20a: "1/1/24" → 2024
    try:
        date(year or 2000, month, day)    # 2000 is a leap year, so "29/2" without a year passes
    except ValueError:
        raise DateError(f"תאריך לא תקין: '{raw}' — אין תאריך כזה בלוח השנה") from None
    return ParsedDate(raw=raw, day=day, month=month, year=year)


def resolve_dates(
    items: list[ParsedDate],
    *,
    start: Optional[date] = None,
    today: Optional[date] = None,
) -> ResolvedDates:
    """Turn a document-ordered sequence of written dates into real dates + warnings.

    Missing year (20b) — reference date, in order:
      1. the previous date in the sequence (written or inferred): its year, +1 when
         the month is earlier than its month (01/12/2025 → "05/01" = 2026);
      2. none → *start* (treatment start date), same rule;
      3. none → the next date in the sequence that has a year, backwards: its year,
         −1 when the month is later than its month ("14/7" before 22/07/2026 → 2026).

    Dates are stored as written, never corrected (21a). Warnings: every inferred
    year; (2) in the future; (3) before *start*.

    Raises DateError when a year is missing and no reference date exists, or the
    inferred date does not exist (29/2 in a non-leap year).
    """
    today = today or date.today()
    result = ResolvedDates(dates=[])
    prev: Optional[date] = None

    for i, item in enumerate(items):
        if item.year is not None:
            d = date(item.year, item.month, item.day)
        else:
            following = next((x for x in items[i + 1:] if x.year is not None), None)
            year = _infer_year(item, prev or start, following)
            try:
                d = date(year, item.month, item.day)
            except ValueError:
                raise DateError(
                    f"תאריך לא תקין: '{item.raw}' — אין תאריך כזה בשנה {year}"
                ) from None
            result.warnings.append(f"התאריך '{item.raw}' נכתב בלי שנה — הונחה השנה {year}")

        if d > today:
            result.warnings.append(f"התאריך '{item.raw}' עתידי — כדאי לבדוק")
        if start is not None and d < start:
            result.warnings.append(
                f"התאריך '{item.raw}' מוקדם מתאריך תחילת הטיפול ({format_date(start)}) — כדאי לבדוק"
            )

        result.dates.append(d)
        prev = d

    return result


def _infer_year(item: ParsedDate, ref: Optional[date], following: Optional[ParsedDate]) -> int:
    """Year for a date written without one — see resolve_dates for the rule."""
    if ref is not None:
        return ref.year + 1 if item.month < ref.month else ref.year
    if following is not None:
        return following.year - 1 if item.month > following.month else following.year
    raise DateError(
        f"התאריך '{item.raw}' נכתב בלי שנה, ואין תאריך אחר שממנו אפשר להסיק אותה"
    )
