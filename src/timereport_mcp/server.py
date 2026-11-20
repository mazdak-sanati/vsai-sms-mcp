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
            "TimeReport MCP server for tenant-scoped Excel timesheets and on-demand SMS. "
            "Tenant identity comes from the authenticated connection, not tool arguments."
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
        """Return workbook identifiers and column metadata for the caller's tenant."""
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return list_workbooks_svc(config, principal).model_dump(mode="json")

    @mcp.tool()
    def inspect_timesheet(workbook_id: str) -> dict[str, Any]:
        """Validate workbook columns/types and summarize record quality."""
        principal, err = require_principal(config.clients, scope="workbooks:read")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return inspect_timesheet_svc(config, principal, workbook_id).model_dump(mode="json")

    @mcp.tool()
    def read_timesheet_entries(workbook_id: str, period: str) -> dict[str, Any]:
        """Return authorized timesheet entries for a YYYY-MM period."""
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
        """Calculate expected vs reported hours for a period."""
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
        """Update one permitted hours cell with version precondition."""
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
        """Query send, suppression, and claim state for an employee/period."""
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
        workbook_id: str,
        employee_id: str,
        period: str,
        template_id: str,
    ) -> dict[str, Any]:
        """Send at most one automated SMS per employee/period/local day."""
        principal, err = require_principal(config.clients, scope="sms:send")
        if err is not None:
            return err.model_dump(mode="json")
        assert principal is not None
        return send_sms_svc(
            config,
            rt.session_factory,
            rt.gateway,
            principal,
            workbook_id=workbook_id,
            employee_id=employee_id,
            period=period,
            template_id=template_id,
        ).model_dump(mode="json")

    @mcp.tool()
    def set_sms_suppression(
        employee_id: str,
        reason: str,
        active: bool,
    ) -> dict[str, Any]:
        """Record or update SMS suppression (opt_out is sticky)."""
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
