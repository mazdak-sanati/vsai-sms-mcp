"""Validated timesheet reading from allowlisted workbooks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook

from timereport_mcp.config import WorkbookConfig

DEFAULT_MAX_ROWS = 50_000

_NAME_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class RowIssue:
    row_number: int
    code: str
    message: str


@dataclass(slots=True)
class TimesheetEntry:
    employee_id: str
    employee_name: str
    telephone: str
    entry_date: date
    hours: float | None
    row_number: int
    identity_is_name_derived: bool
    hours_missing: bool = False


@dataclass(slots=True)
class TimesheetRead:
    workbook_id: str
    version: str
    sheet: str
    entries: list[TimesheetEntry] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_ok: bool = True
    error_code: str | None = None
    error_message: str | None = None


def file_version(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug_employee_name(name: str) -> str:
    collapsed = " ".join(name.casefold().split())
    slug = _NAME_SLUG_RE.sub("-", collapsed).strip("-")
    return slug or "unnamed"


def _header_map(headers: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, value in enumerate(headers):
        if value is None:
            continue
        key = str(value).strip()
        if key and key not in mapping:
            mapping[key] = idx
    return mapping


def _cell_is_formula(cell: Any) -> bool:
    value = getattr(cell, "value", None)
    return isinstance(value, str) and value.startswith("=")


def _parse_date(value: Any, *, row_number: int) -> tuple[date | None, RowIssue | None]:
    if value is None or value == "":
        return None, RowIssue(row_number, "validation_error", "Date is required.")
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if isinstance(value, str):
        text = value.strip()
        # Accept only exact ISO dates — reject locale-ambiguous forms.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text), None
            except ValueError:
                return None, RowIssue(row_number, "validation_error", f"Invalid date: {text}")
        return None, RowIssue(
            row_number,
            "validation_error",
            f"Ambiguous or unsupported date text: {text}",
        )
    return None, RowIssue(row_number, "validation_error", f"Unsupported date type: {type(value).__name__}")


def _parse_hours(value: Any, *, row_number: int, is_formula: bool) -> tuple[float | None, RowIssue | None, bool]:
    if is_formula:
        return None, RowIssue(
            row_number,
            "formula_not_supported",
            "Formula cells are not supported for hours.",
        ), False
    if value is None or value == "":
        return None, None, True
    if isinstance(value, bool):
        return None, RowIssue(row_number, "validation_error", "Boolean hours are not allowed."), False
    if isinstance(value, time):
        return None, RowIssue(row_number, "validation_error", "Time values are not hours."), False
    try:
        if isinstance(value, Decimal):
            hours = float(value)
        else:
            hours = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None, RowIssue(row_number, "validation_error", f"Non-numeric hours: {value!r}"), False
    if hours < 0 or hours > 24:
        return None, RowIssue(row_number, "validation_error", f"Hours out of range: {hours}"), False
    # At most two decimal places.
    quantized = round(hours, 2)
    if abs(quantized - hours) > 1e-9:
        return None, RowIssue(
            row_number,
            "validation_error",
            f"Hours may have at most two decimal places: {hours}",
        ), False
    return quantized, None, False


def read_timesheet(
    workbook_cfg: WorkbookConfig,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    period: str | None = None,
) -> TimesheetRead:
    path = workbook_cfg.path
    result = TimesheetRead(
        workbook_id=workbook_cfg.workbook_id,
        version="",
        sheet=workbook_cfg.sheet,
    )
    if not path.is_file():
        result.schema_ok = False
        result.error_code = "workbook_unreadable"
        result.error_message = "Workbook file not found."
        return result

    try:
        result.version = file_version(path)
        # data_only=False so formulas remain detectable as strings starting with '='.
        wb: Workbook = load_workbook(path, read_only=False, data_only=False)
    except Exception as exc:  # noqa: BLE001 — map any openpyxl/zip failure
        result.schema_ok = False
        result.error_code = "workbook_unreadable"
        result.error_message = f"Unable to open workbook: {exc}"
        return result

    try:
        if workbook_cfg.sheet not in wb.sheetnames:
            result.schema_ok = False
            result.error_code = "schema_mismatch"
            result.error_message = f"Sheet {workbook_cfg.sheet!r} not found."
            return result
        ws = wb[workbook_cfg.sheet]
        rows_iter = ws.iter_rows()
        try:
            header_row = next(rows_iter)
        except StopIteration:
            result.schema_ok = False
            result.error_code = "schema_mismatch"
            result.error_message = "Workbook has no header row."
            return result

        headers = _header_map([cell.value for cell in header_row])
        cols = workbook_cfg.columns
        required = {
            "employee_name": cols.employee_name,
            "telephone": cols.telephone,
            "date": cols.date,
            "hours": cols.hours,
        }
        missing = [logical for logical, header in required.items() if header not in headers]
        if missing:
            result.schema_ok = False
            result.error_code = "schema_mismatch"
            result.error_message = f"Missing required columns: {missing}"
            return result

        id_col = cols.employee_id
        id_idx = headers.get(id_col) if id_col else None
        if id_col and id_idx is None:
            result.schema_ok = False
            result.error_code = "schema_mismatch"
            result.error_message = f"Configured employee_id column {id_col!r} not found."
            return result

        period_start: date | None = None
        period_end: date | None = None
        if period:
            year_s, month_s = period.split("-", 1)
            year, month = int(year_s), int(month_s)
            period_start = date(year, month, 1)
            if month == 12:
                period_end = date(year + 1, 1, 1)
            else:
                period_end = date(year, month + 1, 1)

        data_rows = 0
        seen_keys: dict[tuple[str, date], int] = {}
        for excel_row in rows_iter:
            data_rows += 1
            if data_rows > max_rows:
                result.schema_ok = False
                result.error_code = "workbook_unreadable"
                result.error_message = f"Workbook exceeds max row limit ({max_rows})."
                result.entries.clear()
                return result

            raw_row = excel_row[0].row if excel_row else None
            row_number = int(raw_row) if raw_row is not None else data_rows + 1
            name_cell = excel_row[headers[cols.employee_name]]
            phone_cell = excel_row[headers[cols.telephone]]
            date_cell = excel_row[headers[cols.date]]
            hours_cell = excel_row[headers[cols.hours]]

            # Skip completely empty rows.
            if all(c.value in (None, "") for c in (name_cell, phone_cell, date_cell, hours_cell)):
                continue

            if _cell_is_formula(name_cell) or _cell_is_formula(phone_cell) or _cell_is_formula(date_cell):
                result.issues.append(
                    RowIssue(row_number, "formula_not_supported", "Formula in identity/date cells.")
                )
                continue

            name = "" if name_cell.value is None else str(name_cell.value).strip()
            if not name:
                result.issues.append(RowIssue(row_number, "validation_error", "Employee name is required."))
                continue

            telephone = "" if phone_cell.value is None else str(phone_cell.value).strip()
            entry_date, date_issue = _parse_date(date_cell.value, row_number=row_number)
            if date_issue:
                result.issues.append(date_issue)
                continue
            assert entry_date is not None

            if period_start is not None and period_end is not None:
                if not (period_start <= entry_date < period_end):
                    continue

            hours, hours_issue, hours_missing = _parse_hours(
                hours_cell.value,
                row_number=row_number,
                is_formula=_cell_is_formula(hours_cell),
            )
            if hours_issue:
                result.issues.append(hours_issue)
                # Still record a stub entry so reporting can mark data_review_required.
                hours = None

            identity_is_name_derived = False
            if id_idx is not None:
                raw_id = excel_row[id_idx].value
                employee_id = "" if raw_id is None else str(raw_id).strip()
                if not employee_id:
                    employee_id = slug_employee_name(name)
                    identity_is_name_derived = True
            else:
                employee_id = slug_employee_name(name)
                identity_is_name_derived = True

            key = (employee_id, entry_date)
            if key in seen_keys:
                result.issues.append(
                    RowIssue(
                        row_number,
                        "validation_error",
                        f"Duplicate entry for {employee_id} on {entry_date.isoformat()}",
                    )
                )
            else:
                seen_keys[key] = row_number

            if identity_is_name_derived and "employee_identity_is_name_derived" not in result.warnings:
                result.warnings.append("employee_identity_is_name_derived")

            result.entries.append(
                TimesheetEntry(
                    employee_id=employee_id,
                    employee_name=name,
                    telephone=telephone,
                    entry_date=entry_date,
                    hours=hours,
                    row_number=row_number,
                    identity_is_name_derived=identity_is_name_derived,
                    hours_missing=hours_missing,
                )
            )
        return result
    finally:
        wb.close()


def inspect_summary(read: TimesheetRead) -> dict[str, Any]:
    return {
        "workbook_id": read.workbook_id,
        "version": read.version,
        "sheet": read.sheet,
        "schema_ok": read.schema_ok,
        "row_count": len(read.entries),
        "issue_count": len(read.issues),
        "issues": [{"row": i.row_number, "code": i.code, "message": i.message} for i in read.issues],
        "warnings": list(read.warnings),
        "error_code": read.error_code,
        "error_message": read.error_message,
        "columns_present": True if read.schema_ok else False,
    }


def entries_payload(read: TimesheetRead) -> list[dict[str, Any]]:
    return [
        {
            "employee_id": e.employee_id,
            "employee_name": e.employee_name,
            "date": e.entry_date.isoformat(),
            "hours": e.hours,
            "hours_missing": e.hours_missing,
        }
        for e in read.entries
    ]
