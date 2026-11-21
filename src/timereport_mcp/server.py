"""MCP tool registration and transport entrypoint."""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from timereport_mcp.auth.context import (
    install_stdio_principal,
    require_principal,
)
from timereport_mcp.auth.token_verifier import RegistryTokenVerifier
from timereport_mcp.config import AppConfig, ConfigError, load_config, require_stdio_tenant
from timereport_mcp.excel.service import (
    inspect_timesheet as inspect_timesheet_svc,
)
from timereport_mcp.excel.service import (
    read_timesheet_entries as read_timesheet_entries_svc,
)
from timereport_mcp.excel.workbooks import list_workbooks as list_workbooks_svc
from timereport_mcp.excel.writer import update_timesheet_entry as update_timesheet_entry_svc
from timereport_mcp.observability.logging import configure_logging, get_logger
from timereport_mcp.reporting.service import (
    get_reporting_status as get_reporting_status_svc,
)
from timereport_mcp.runtime import Runtime, build_runtime
from timereport_mcp.sms.service import (
    get_sms_status as get_sms_status_svc,
)
from timereport_mcp.sms.service import (
    send_sms as send_sms_svc,
)
from timereport_mcp.sms.service import (
    set_sms_suppression as set_sms_suppression_svc,
)

logger = get_logger(__name__)


def create_server(
    config: AppConfig,
    *,
    enable_http_auth: bool = True,
    runtime: Runtime | None = None,
) -> MCPServer:
    """Build an MCPServer with tools bound to the given config."""
    configure_logging()
    if config.clients.has_synthetic_examples():
        logger.warning(
            "Synthetic example token hashes are loaded; bind to loopback only "
            "and replace them before any non-local deployment."
        )

    rt = runtime or build_runtime(config)

    kwargs: dict[str, Any] = {
        "name": "timereport-mcp",
        "instructions": (
            "TimeReport MCP server: tenant-scoped Excel timesheets and on-demand SMS tools. "
            "You are a client of this server; you decide when to call tools. "
            "This server never schedules checks or sends SMS by itself.\n\n"
            "Identity: tenant and scopes come from the connection "
            "(HTTP Bearer token or stdio TIMEREPORT_STDIO_TENANT). "
            "Never pass tenant_id, filesystem paths, SMS passwords, or LLM keys as tool arguments.\n\n"
            "Typical flow: list_workbooks → get_reporting_status(period=YYYY-MM). "
            "Completion is only from get_reporting_status (Excel + policy), never from SMS replies.\n\n"
            "Writes: call inspect_timesheet or read first, then update_timesheet_entry with "
            "expected_version equal to the returned version.\n\n"
            "SMS: send_sms requires caller-provided to (E.164) and message body. "
            "This server does not choose recipients or compose reminder text. "
            "employee_id + period are for suppression and same-day idempotency only. "
            "At most one automated send per employee/period/local calendar day; "
            "a repeat returns ok with replayed=true and does not send again. "
            "Check get_sms_status or suppression errors before retrying.\n\n"
            "All tools return JSON {ok, data, error}. Check ok before using data. "
            "Full reference: docs/mcp_api.md in the server repository."
        ),
    }
    if enable_http_auth:
        kwargs["token_verifier"] = RegistryTokenVerifier(
            config.clients, config.settings.resource_server_url
        )
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(config.settings.auth_issuer_url),
            resource_server_url=AnyHttpUrl(config.settings.resource_server_url),
            required_scopes=[],
            validate_token_resource=True,
        )

    mcp = MCPServer(**kwargs)

    @mcp.tool()
    def list_workbooks() -> dict[str, Any]:
        """List opaque workbook_id values and column mappings for this tenant.

        Call this first. Do not guess paths. Response envelope: {ok, data, error}.
        """
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return list_workbooks_svc(config, principal).model_dump(mode="json")

    @mcp.tool()
    def inspect_timesheet(workbook_id: str) -> dict[str, Any]:
        """Validate timesheet schema and return quality summary plus content version.

        workbook_id: from list_workbooks. Use returned version before update_timesheet_entry.
        """
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return inspect_timesheet_svc(config, principal, workbook_id).model_dump(mode="json")

    @mcp.tool()
    def read_timesheet_entries(workbook_id: str, period: str) -> dict[str, Any]:
        """Read timesheet rows for one calendar month.

        workbook_id: from list_workbooks.
        period: YYYY-MM (example 2026-10).
        """
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return read_timesheet_entries_svc(
            config, principal, workbook_id, period
        ).model_dump(mode="json")

    @mcp.tool()
    def get_reporting_status(
        workbook_id: str,
        period: str,
        employee_id: str | None = None,
    ) -> dict[str, Any]:
        """Compare expected vs reported hours for a period.

        period: YYYY-MM. Optional employee_id limits to one person.
        status values: complete, incomplete, policy_review_required, data_review_required.
        Use this to decide who is incomplete; do not infer completion from SMS replies.
        """
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return get_reporting_status_svc(
            config, principal, workbook_id, period, employee_id=employee_id
        ).model_dump(mode="json")

    @mcp.tool()
    def update_timesheet_entry(
        workbook_id: str,
        employee_id: str,
        date: str,
        hours: float,
        expected_version: str,
        create_if_missing: bool = False,
    ) -> dict[str, Any]:
        """Update one employee's hours for one date with optimistic concurrency.

        date: YYYY-MM-DD. hours: 0-24 with at most two decimals.
        expected_version: SHA-256 from inspect_timesheet or read_timesheet_entries.
        On version_conflict, re-read and retry with the new version.
        """
        principal, err = require_principal(config.clients, scope="workbooks:write")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        try:
            entry_date = date_from_iso(date)
        except ValueError:
            from timereport_mcp.models import ToolResult

            return ToolResult.failure(
                "validation_error",
                "date must be YYYY-MM-DD",
            ).model_dump(mode="json")
        return update_timesheet_entry_svc(
            config,
            principal,
            workbook_id=workbook_id,
            employee_id=employee_id,
            entry_date=entry_date,
            hours=hours,
            expected_version=expected_version,
            create_if_missing=create_if_missing,
        ).model_dump(mode="json")

    @mcp.tool()
    def get_sms_status(
        employee_id: str,
        period: str,
        local_date: str | None = None,
    ) -> dict[str, Any]:
        """Return SMS claim and suppression state for an employee and period.

        period: YYYY-MM. local_date: optional YYYY-MM-DD (defaults to today in tenant TZ).
        Does not send a message.
        """
        principal, err = require_principal(config.clients, scope="sms:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        day = None
        if local_date is not None:
            try:
                day = date_from_iso(local_date)
            except ValueError:
                from timereport_mcp.models import ToolResult

                return ToolResult.failure(
                    "validation_error",
                    "local_date must be YYYY-MM-DD",
                ).model_dump(mode="json")
        return get_sms_status_svc(
            config,
            rt.session_factory,
            principal,
            employee_id=employee_id,
            period=period,
            local_date=day,
        ).model_dump(mode="json")

    @mcp.tool()
    def send_sms(
        to: str,
        message: str,
        employee_id: str,
        period: str,
    ) -> dict[str, Any]:
        """Send one SMS using caller-provided destination and message body.

        to: destination telephone (prefer E.164, e.g. +4670...).
        message: exact SMS body to send (composed by the calling agent).
        employee_id + period: used only for suppression and same-day idempotency.
        At most one send per employee/period/local day; repeats return replayed=true
        without sending again. Provider credentials are never tool arguments.
        """
        principal, err = require_principal(config.clients, scope="sms:send")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return send_sms_svc(
            config,
            rt.session_factory,
            rt.gateway,
            principal,
            to=to,
            message=message,
            employee_id=employee_id,
            period=period,
        ).model_dump(mode="json")

    @mcp.tool()
    def set_sms_suppression(
        employee_id: str,
        reason: str,
        active: bool,
    ) -> dict[str, Any]:
        """Set SMS suppression for an employee.

        reason: opt_out or admin. opt_out cannot be cleared with active=false.
        Active suppression causes send_sms to fail with sms_suppressed.
        """
        principal, err = require_principal(config.clients, scope="sms:admin")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return set_sms_suppression_svc(
            config,
            rt.session_factory,
            principal,
            employee_id=employee_id,
            reason=reason,
            active=active,
        ).model_dump(mode="json")

    # Attach config/runtime for tests without exposing them as tool args.
    mcp._timereport_config = config  # type: ignore[attr-defined]
    mcp._timereport_runtime = rt  # type: ignore[attr-defined]
    return mcp


def date_from_iso(value: str) -> date:
    return date.fromisoformat(value)


def build_server_from_env(
    environ: dict[str, str] | None = None,
) -> tuple[MCPServer, AppConfig, Runtime]:
    env = dict(os.environ if environ is None else environ)
    config_path = Path(env.get("TIMEREPORT_CONFIG", "config/tenants.example.toml"))
    config = load_config(config_path, environ=env)
    runtime = build_runtime(config)
    return create_server(config, runtime=runtime), config, runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="timereport-mcp")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args(argv)

    try:
        mcp, config, _runtime = build_server_from_env()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    if args.transport == "stdio":
        try:
            tenant_id = require_stdio_tenant(config)
        except ConfigError as exc:
            raise SystemExit(f"Configuration error: {exc}") from exc
        # Bind a stdio principal with full configured scopes for that tenant's first client.
        clients = config.clients.clients_for_tenant(tenant_id)
        if not clients:
            raise SystemExit(f"No clients configured for stdio tenant {tenant_id}")
        principal = config.clients.resolve_client_id(clients[0].client_id)
        assert principal is not None
        install_stdio_principal(principal)
        logger.info("Starting stdio transport for tenant %s", tenant_id)
        mcp.run(transport="stdio")
        return

    logger.info(
        "Starting streamable-http on %s:%s",
        config.settings.http_host,
        config.settings.http_port,
    )
    mcp.run(
        transport="streamable-http",
        host=config.settings.http_host,
        port=config.settings.http_port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
