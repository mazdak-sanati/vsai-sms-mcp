"""Helpers to build synthetic .xlsx fixtures for tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_timesheet(
    path: Path,
    rows: Sequence[dict[str, Any]],
    *,
    headers: Sequence[str] | None = None,
    sheet: str = "Timesheet",
    extra_sheet: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet
    cols = list(headers) if headers is not None else [
        "employee_id",
        "employee_name",
        "telephone",
        "date",
        "hours",
    ]
    ws.append(list(cols))
    for row in rows:
        ws.append([row.get(c) for c in cols])

    if extra_sheet is not None:
        other = wb.create_sheet(extra_sheet.get("name", "Other"))
        other.append(extra_sheet.get("headers", ["note"]))
        for values in extra_sheet.get("rows", [["keep-me"]]):
            other.append(values)

    wb.save(path)
    wb.close()
    return path


def october_full_rows(
    *,
    employee_id: str = "synthetic-employee-a1",
    name: str = "Synthetic Person A",
    phone: str = "+46700000001",
    hours: float = 8.0,
    skip_dates: set[date] | None = None,
) -> list[dict[str, Any]]:
    """22 Mon–Fri days in October 2026."""
    skip = skip_dates or set()
    rows: list[dict[str, Any]] = []
    day = date(2026, 10, 1)
    while day.month == 10:
        if day.weekday() < 5 and day not in skip:
            rows.append(
                {
                    "employee_id": employee_id,
                    "employee_name": name,
                    "telephone": phone,
                    "date": day,
                    "hours": hours,
                }
            )
        day = date.fromordinal(day.toordinal() + 1)
    return rows
