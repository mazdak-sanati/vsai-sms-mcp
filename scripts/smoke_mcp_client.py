#!/usr/bin/env python3
"""Smoke client: exercise tools against synthetic fixtures + mock SMS."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mcp import Client  # noqa: E402
from tests.fixtures.workbook_factory import october_full_rows, write_timesheet  # noqa: E402

from timereport_mcp.auth.context import install_test_principal  # noqa: E402
from timereport_mcp.config import load_config  # noqa: E402
from timereport_mcp.runtime import build_runtime  # noqa: E402
from timereport_mcp.server import create_server  # noqa: E402
from timereport_mcp.sms.gateway import MockSmsGateway  # noqa: E402


async def run_for_token(
    token: str,
    workbook_id: str,
    employee_id: str,
    fixture: Path,
    *,
    employee_name: str,
) -> None:
    write_timesheet(
        fixture,
        october_full_rows(employee_id=employee_id, name=employee_name),
    )
    data_dir = ROOT / "data" / f"smoke-{workbook_id}"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = data_dir / "sms.db"
    env = {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{db.as_posix()}",
    }

    config = load_config(ROOT / "config" / "tenants.example.toml", environ=env)
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    mcp = create_server(config, enable_http_auth=False, runtime=runtime)
    principal = config.clients.resolve_token(token)
    assert principal is not None
    install_test_principal(principal)
    try:
        async with Client(mcp) as client:
            listed = await client.call_tool("list_workbooks", {})
            print(token[-12:], "list_workbooks", listed.structured_content)
            status = await client.call_tool(
                "get_reporting_status",
                {"workbook_id": workbook_id, "period": "2026-10"},
            )
            print(token[-12:], "get_reporting_status", status.structured_content)
            sms = await client.call_tool(
                "send_sms",
                {
                    "workbook_id": workbook_id,
                    "employee_id": employee_id,
                    "period": "2026-10",
                    "template_id": "timesheet_reminder",
                },
            )
            print(token[-12:], "send_sms", sms.structured_content)
            sms2 = await client.call_tool(
                "send_sms",
                {
                    "workbook_id": workbook_id,
                    "employee_id": employee_id,
                    "period": "2026-10",
                    "template_id": "timesheet_reminder",
                },
            )
            print(token[-12:], "send_sms_replay", sms2.structured_content)
    finally:
        install_test_principal(None)


async def main() -> None:
    await run_for_token(
        "tenant-a-token-synthetic",
        "tenant-a-timesheet",
        "synthetic-employee-a1",
        ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx",
        employee_name="Synthetic Person A",
    )
    await run_for_token(
        "tenant-b-token-synthetic",
        "tenant-b-timesheet",
        "synthetic-person-b",
        ROOT / "tests" / "fixtures" / "tenant-b" / "timesheet.xlsx",
        employee_name="Synthetic Person B",
    )


if __name__ == "__main__":
    asyncio.run(main())
