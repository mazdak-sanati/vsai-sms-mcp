"""M0 smoke: package imports and version are available."""

from __future__ import annotations

import timereport_mcp


def test_package_import_exposes_version() -> None:
    assert timereport_mcp.__version__ == "0.1.0"


def test_tool_result_helpers() -> None:
    from timereport_mcp.models import ToolResult

    ok = ToolResult.success({"workbook_id": "synthetic-a"})
    assert ok.ok is True
    assert ok.data == {"workbook_id": "synthetic-a"}

    err = ToolResult.failure("not_found", "Workbook not found.")
    assert err.ok is False
    assert err.error is not None
    assert err.error.code == "not_found"
