"""Reporting tool service."""

from __future__ import annotations

from timereport_mcp.auth.principals import Principal
from timereport_mcp.config import AppConfig
from timereport_mcp.excel.reader import read_timesheet
from timereport_mcp.excel.service import validate_period
from timereport_mcp.excel.workbooks import resolve_workbook
from timereport_mcp.models import ToolResult
from timereport_mcp.reporting.policy import compute_reporting_status, statuses_payload


def get_reporting_status(
    config: AppConfig,
    principal: Principal,
    workbook_id: str,
    period: str,
    employee_id: str | None = None,
) -> ToolResult:
    period_err = validate_period(period)
    if period_err is not None:
        return period_err
    workbook, err = resolve_workbook(config, principal, workbook_id)
    if err is not None:
        return err
    assert workbook is not None
    read = read_timesheet(workbook, period=period)
    if read.error_code:
        return ToolResult.failure(
            read.error_code,
            read.error_message or "Workbook error",
        )
    statuses = compute_reporting_status(
        config,
        principal.tenant_id,
        read,
        period,
        employee_id=employee_id,
    )
    return ToolResult.success(
        {
            "workbook_id": workbook_id,
            "period": period,
            "version": read.version,
            "employees": statuses_payload(statuses),
        }
    )
