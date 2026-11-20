"""Controlled single-cell timesheet updates with lock, backup, and version check."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from openpyxl import load_workbook

from timereport_mcp.auth.principals import Principal
from timereport_mcp.config import AppConfig, WorkbookConfig
from timereport_mcp.excel.reader import file_version, slug_employee_name
from timereport_mcp.excel.workbooks import resolve_workbook
from timereport_mcp.models import ToolResult
from timereport_mcp.persistence.audit import FileAuditLog

LOCK_TIMEOUT_SECONDS = 10.0
MAX_BACKUPS = 20


def _lock_path(data_dir: Path, tenant_id: str, workbook_id: str) -> Path:
    directory = data_dir / "locks" / tenant_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{workbook_id}.lock"


def _backup_dir(data_dir: Path, tenant_id: str, workbook_id: str) -> Path:
    directory = data_dir / "backups" / tenant_id / workbook_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _prune_backups(directory: Path) -> None:
    backups = sorted(directory.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in backups[MAX_BACKUPS:]:
        stale.unlink(missing_ok=True)


def _find_row(
    ws,
    columns: dict[str, int],
    *,
    employee_id: str,
    employee_id_header: str | None,
    name_header: str,
    date_header: str,
    target_date: date,
) -> int | None:
    for row in ws.iter_rows(min_row=2):
        date_cell = row[columns[date_header]]
        value = date_cell.value
        if isinstance(value, datetime):
            row_date = value.date()
        elif isinstance(value, date):
            row_date = value
        else:
            continue
        if row_date != target_date:
            continue
        if employee_id_header and employee_id_header in columns:
            raw = row[columns[employee_id_header]].value
            rid = "" if raw is None else str(raw).strip()
            if rid == employee_id:
                return date_cell.row
        else:
            name = str(row[columns[name_header]].value or "").strip()
            if slug_employee_name(name) == employee_id:
                return date_cell.row
    return None


def update_timesheet_entry(
    config: AppConfig,
    principal: Principal,
    *,
    workbook_id: str,
    employee_id: str,
    entry_date: date,
    hours: float,
    expected_version: str,
    create_if_missing: bool = False,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> ToolResult:
    if hours < 0 or hours > 24 or round(hours, 2) != hours:
        return ToolResult.failure(
            "validation_error",
            "hours must be between 0 and 24 with at most two decimals.",
        )

    workbook, err = resolve_workbook(config, principal, workbook_id)
    if err is not None:
        return err
    assert workbook is not None

    data_dir = config.settings.data_dir
    lock = FileLock(str(_lock_path(data_dir, principal.tenant_id, workbook_id)))
    try:
        lock.acquire(timeout=lock_timeout)
    except Timeout:
        return ToolResult.failure("lock_timeout", "Could not acquire workbook lock.")

    try:
        return _update_locked(
            config,
            principal,
            workbook,
            employee_id=employee_id,
            entry_date=entry_date,
            hours=hours,
            expected_version=expected_version,
            create_if_missing=create_if_missing,
        )
    finally:
        lock.release()


def _update_locked(
    config: AppConfig,
    principal: Principal,
    workbook: WorkbookConfig,
    *,
    employee_id: str,
    entry_date: date,
    hours: float,
    expected_version: str,
    create_if_missing: bool,
) -> ToolResult:
    path = workbook.path
    if not path.is_file():
        return ToolResult.failure("workbook_unreadable", "Workbook file not found.")

    current = file_version(path)
    if current != expected_version:
        return ToolResult.failure(
            "version_conflict",
            "Workbook version did not match.",
            details={"expected_version": expected_version, "actual_version": current},
        )

    backup_dir = _backup_dir(config.settings.data_dir, principal.tenant_id, workbook.workbook_id)
    backup_path = backup_dir / f"{current}.xlsx"
    try:
        shutil.copy2(path, backup_path)
    except OSError as exc:
        return ToolResult.failure(
            "workbook_unreadable",
            f"Failed to create backup: {exc}",
        )
    _prune_backups(backup_dir)

    try:
        wb = load_workbook(path)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure("workbook_unreadable", f"Unable to open workbook: {exc}")

    try:
        if workbook.sheet not in wb.sheetnames:
            return ToolResult.failure("schema_mismatch", f"Sheet {workbook.sheet!r} not found.")
        ws = wb[workbook.sheet]
        headers = {
            str(cell.value).strip(): idx
            for idx, cell in enumerate(next(ws.iter_rows(min_row=1, max_row=1)))
            if cell.value is not None
        }
        cols = workbook.columns
        for required in (cols.employee_name, cols.telephone, cols.date, cols.hours):
            if required not in headers:
                return ToolResult.failure(
                    "schema_mismatch",
                    f"Missing column {required!r}",
                )

        row_number = _find_row(
            ws,
            headers,
            employee_id=employee_id,
            employee_id_header=cols.employee_id,
            name_header=cols.employee_name,
            date_header=cols.date,
            target_date=entry_date,
        )

        old_hours: float | None = None
        if row_number is None:
            if not create_if_missing:
                return ToolResult.failure(
                    "not_found",
                    "Timesheet entry not found.",
                    details={"employee_id": employee_id, "date": entry_date.isoformat()},
                )
            # Append a new row with mapped identity/date/hours only.
            max_col = max(headers.values()) + 1
            new_row: list[object | None] = [None] * max_col
            if cols.employee_id and cols.employee_id in headers:
                new_row[headers[cols.employee_id]] = employee_id
            new_row[headers[cols.employee_name]] = employee_id
            new_row[headers[cols.telephone]] = ""
            new_row[headers[cols.date]] = entry_date
            new_row[headers[cols.hours]] = hours
            ws.append(new_row)
            row_number = ws.max_row
        else:
            hours_cell = ws.cell(row=row_number, column=headers[cols.hours] + 1)
            if isinstance(hours_cell.value, str) and hours_cell.value.startswith("="):
                return ToolResult.failure(
                    "formula_not_supported",
                    "Cannot overwrite a formula hours cell.",
                )
            raw_hours = hours_cell.value
            if raw_hours is not None and raw_hours != "":
                try:
                    old_hours = float(raw_hours)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    old_hours = None
            hours_cell.value = hours  # type: ignore[assignment]

        # Atomic replace via same-directory temp file.
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            wb.save(tmp_path)
            # Flush is handled by openpyxl close via save.
            os.replace(tmp_path, path)
            if sys.platform != "win32":
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    finally:
        wb.close()

    new_version = file_version(path)
    audit = FileAuditLog(config.settings.data_dir / "audit" / f"{principal.tenant_id}.jsonl")
    audit.append(
        {
            "type": "timesheet_update",
            "actor_client_id": principal.client_id,
            "tenant_id": principal.tenant_id,
            "workbook_id": workbook.workbook_id,
            "employee_id": employee_id,
            "date": entry_date.isoformat(),
            "old_hours": old_hours,
            "new_hours": hours,
            "previous_version": current,
            "new_version": new_version,
        }
    )
    return ToolResult.success(
        {
            "workbook_id": workbook.workbook_id,
            "employee_id": employee_id,
            "date": entry_date.isoformat(),
            "hours": hours,
            "previous_version": current,
            "version": new_version,
            "backup": str(backup_path.name),
        }
    )
