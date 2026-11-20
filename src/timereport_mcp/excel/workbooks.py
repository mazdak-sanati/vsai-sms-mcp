"""Workbook listing and tenant-scoped resolution helpers."""

from __future__ import annotations

from timereport_mcp.auth.principals import Principal
from timereport_mcp.config import AppConfig, WorkbookConfig
from timereport_mcp.models import ToolResult


def list_workbooks(config: AppConfig, principal: Principal) -> ToolResult:
    tenant = config.tenants.get(principal.tenant_id)
    if tenant is None:
        return ToolResult.failure("not_found", "Tenant not found.")
    items = []
    for workbook in tenant.workbooks:
        cols = {
            "employee_name": workbook.columns.employee_name,
            "telephone": workbook.columns.telephone,
            "date": workbook.columns.date,
            "hours": workbook.columns.hours,
        }
        if workbook.columns.employee_id:
            cols["employee_id"] = workbook.columns.employee_id
        items.append(
            {
                "workbook_id": workbook.workbook_id,
                "label": workbook.label,
                "sheet": workbook.sheet,
                "columns": cols,
            }
        )
    return ToolResult.success({"workbooks": items})


def resolve_workbook(
    config: AppConfig,
    principal: Principal,
    workbook_id: str,
) -> tuple[WorkbookConfig | None, ToolResult | None]:
    workbook = config.workbook_for(principal.tenant_id, workbook_id)
    if workbook is None:
        return None, ToolResult.failure("not_found", "Workbook not found.")
    return workbook, None
