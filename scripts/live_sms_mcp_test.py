#!/usr/bin/env python3
"""One-off live SMS test through the TimeReport MCP `send_sms` tool.

Requires filled credentials in tests/local_test.env and an explicit confirm flag:

    python scripts/live_sms_mcp_test.py --i-understand-this-sends-a-real-sms

This is not part of the default pytest suite. It charges a real 46elks send.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "tests" / "local_test.env"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mcp import Client  # noqa: E402

from tests.fixtures.workbook_factory import write_timesheet  # noqa: E402
from timereport_mcp.auth.context import install_test_principal  # noqa: E402
from timereport_mcp.config import ConfigError, load_config  # noqa: E402
from timereport_mcp.runtime import build_runtime  # noqa: E402
from timereport_mcp.server import create_server  # noqa: E402


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}. Copy tests/local_test.env.example to "
            "tests/local_test.env and fill in credentials."
        )
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_filled(env: dict[str, str], *keys: str) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise SystemExit(
            "Fill these keys in tests/local_test.env before running: "
            + ", ".join(missing)
        )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send one real SMS via the MCP send_sms tool (46elks)."
    )
    parser.add_argument(
        "--i-understand-this-sends-a-real-sms",
        action="store_true",
        required=True,
        help="Required confirmation that this call may charge and deliver a live SMS.",
    )
    parser.add_argument(
        "--period",
        default=date.today().strftime("%Y-%m"),
        help="Reporting period YYYY-MM used in the SMS claim key (default: current month).",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help=(
            "Use a unique employee_id so a new claim key is created and another "
            "real SMS can be sent today (bypasses same-day replay for this test only)."
        ),
    )
    args = parser.parse_args()

    file_env = load_env_file(ENV_PATH)
    require_filled(
        file_env,
        "ELKS_API_USERNAME",
        "ELKS_API_PASSWORD",
        "ELKS_FROM",
        "TEST_SMS_TO",
    )
    if file_env.get("SMS_PROVIDER", "").lower() != "elks":
        raise SystemExit("SMS_PROVIDER must be elks in tests/local_test.env")
    if file_env.get("SMS_LIVE_SEND", "").lower() not in ("1", "true", "yes"):
        raise SystemExit("SMS_LIVE_SEND must be true in tests/local_test.env")

    recipient = file_env["TEST_SMS_TO"]
    if args.force_new:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        employee_id = f"live-sms-test-employee-{stamp}"
    else:
        employee_id = "live-sms-test-employee"
    workbook_id = "tenant-a-timesheet"
    fixture = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    today = date.today()

    write_timesheet(
        fixture,
        [
            {
                "employee_id": employee_id,
                "employee_name": "Live SMS Test Person",
                "telephone": recipient,
                "date": today,
                "hours": 8.0,
            }
        ],
    )

    data_dir = ROOT / "data" / "live-sms-mcp-test"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "sms.db"

    environ = {
        **dict(os.environ),
        **file_env,
        "TIMEREPORT_CONFIG": str(ROOT / "config" / "tenants.example.toml"),
        "TIMEREPORT_DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "SMS_PROVIDER": "elks",
        "SMS_LIVE_SEND": "true",
    }

    try:
        config = load_config(environ["TIMEREPORT_CONFIG"], environ=environ)
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    runtime = build_runtime(config)
    mcp = create_server(config, enable_http_auth=False, runtime=runtime)
    principal = config.clients.resolve_token("tenant-a-token-synthetic")
    if principal is None:
        raise SystemExit("Could not resolve synthetic tenant-a principal from example config.")

    install_test_principal(principal)
    try:
        async with Client(mcp) as client:
            print("Calling MCP tool send_sms (live 46elks)…")
            print(f"  workbook_id={workbook_id}")
            print(f"  employee_id={employee_id}")
            print(f"  period={args.period}")
            print(f"  to={recipient}")
            print(f"  from={file_env['ELKS_FROM']}")
            result = await client.call_tool(
                "send_sms",
                {
                    "workbook_id": workbook_id,
                    "employee_id": employee_id,
                    "period": args.period,
                    "template_id": "timesheet_reminder",
                },
            )
            payload = result.structured_content
            print("Result:", payload)
            data = (payload or {}).get("data") or {}
            if payload and payload.get("ok") and data.get("replayed"):
                print()
                print("No new SMS was sent.")
                print(
                    "Reason: at-most-once claim already exists for "
                    f"employee={data.get('employee_id')} period={data.get('period')} "
                    f"local_date={data.get('local_date')} state={data.get('state')}."
                )
                print(
                    "To send another real SMS today, re-run with --force-new, "
                    "or wait until the next local calendar day (Europe/Stockholm)."
                )
                raise SystemExit(2)
            if payload and payload.get("ok"):
                print(
                    f"Live SMS accepted by provider "
                    f"(state={data.get('state')}, id={data.get('provider_message_id')})."
                )
            else:
                raise SystemExit(f"send_sms failed: {payload}")
    finally:
        install_test_principal(None)
        gateway = runtime.gateway
        close = getattr(gateway, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    asyncio.run(main())
