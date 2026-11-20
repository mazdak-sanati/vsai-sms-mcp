"""Excel tool service wrappers."""

from __future__ import annotations

import re

from timereport_mcp.auth.principals import Principal
from timereport_mcp.config import AppConfig
from timereport_mcp.excel.reader import (
    DEFAULT_MAX_ROWS,
    entries_payload,
    inspect_summary,
    read_timesheet,
)
from timereport_mcp.excel.workbooks import resolve_workbook
from timereport_mcp.models import ToolResult

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_period(period: str) -> ToolResult | None:
    if not _PERIOD_RE.fullmatch(period):
        return ToolResult.failure(
            "validation_error",
            "period must be YYYY-MM",
            details={"period": period},
        )
    return None


def inspect_timesheet(
    config: AppConfig,
    principal: Principal,
    workbook_id: str,
    *,
    max_rows: int | None = None,
) -> ToolResult:
    workbook, err = resolve_workbook(config, principal, workbook_id)
    if err is not None:
        return err
    assert workbook is not None
    kwargs = {}
    if max_rows is not None:
        kwargs["max_rows"] = max_rows
    read = read_timesheet(workbook, **kwargs)
    if read.error_code:
        return ToolResult.failure(
            read.error_code,
            read.error_message or "Workbook error",
            details=inspect_summary(read),
        )
    return ToolResult.success(inspect_summary(read))


def read_timesheet_entries(
    config: AppConfig,
    principal: Principal,
    workbook_id: str,
    period: str,
    *,
    max_rows: int | None = None,
) -> ToolResult:
    period_err = validate_period(period)
    if period_err is not None:
        return period_err
    workbook, err = resolve_workbook(config, principal, workbook_id)
    if err is not None:
        return err
    assert workbook is not None
    read = read_timesheet(
        workbook,
        period=period,
        max_rows=max_rows if max_rows is not None else DEFAULT_MAX_ROWS,
    )
    if read.error_code:
        return ToolResult.failure(
            read.error_code,
            read.error_message or "Workbook error",
            details=inspect_summary(read),
        )
    return ToolResult.success(
        {
            "workbook_id": workbook.workbook_id,
            "period": period,
            "version": read.version,
            "entries": entries_payload(read),
            "issues": [
                {"row": i.row_number, "code": i.code, "message": i.message}
                for i in read.issues
            ],
            "warnings": list(read.warnings),
        }
    )
