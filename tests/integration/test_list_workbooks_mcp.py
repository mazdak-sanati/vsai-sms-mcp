"""M2 acceptance: MCP shell and list_workbooks isolation."""

from __future__ import annotations

import io
import threading
from contextlib import redirect_stdout
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import Client

from timereport_mcp.auth.context import install_test_principal
from timereport_mcp.config import load_config
from timereport_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
TOKEN_B = "tenant-b-token-synthetic"


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(ROOT / "data" / "test"),
        "TIMEREPORT_RESOURCE_SERVER_URL": "http://127.0.0.1:8765/mcp",
        "TIMEREPORT_AUTH_ISSUER_URL": "https://auth.example.invalid",
    }
    env.update(overrides)
    return env


@pytest.fixture
def config():
    return load_config(EXAMPLE_CONFIG, environ=_env())


@pytest.mark.asyncio
async def test_in_memory_list_workbooks_with_explicit_principal(config) -> None:
    mcp = create_server(config, enable_http_auth=False)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    install_test_principal(principal)
    try:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_list = tools.tools if hasattr(tools, "tools") else tools
            names = {t.name for t in tool_list}
            assert "list_workbooks" in names
            schema = next(t for t in tool_list if t.name == "list_workbooks")
            props = (schema.input_schema or {}).get("properties") or {}
            assert "tenant_id" not in props

            result = await client.call_tool("list_workbooks", {})
            payload = result.structured_content
            assert payload is not None
            assert payload["ok"] is True
            assert payload["data"] is not None
            ids = {w["workbook_id"] for w in payload["data"]["workbooks"]}
            assert ids == {"tenant-a-timesheet"}
            assert "tenant-b-timesheet" not in ids
            for workbook in payload["data"]["workbooks"]:
                assert "path" not in workbook
    finally:
        install_test_principal(None)


@pytest.mark.asyncio
async def test_http_cross_tenant_isolation_and_401(config) -> None:
    # Point resource URL at the test listener.
    port = 8765
    resource = f"http://127.0.0.1:{port}/mcp"
    cfg = load_config(
        EXAMPLE_CONFIG,
        environ=_env(TIMEREPORT_RESOURCE_SERVER_URL=resource),
    )
    mcp = create_server(cfg, enable_http_auth=True)
    app = mcp.streamable_http_app(stateless_http=True, json_response=True)

    config_uvicorn = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config_uvicorn)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        # Wait until listening.
        for _ in range(50):
            try:
                async with httpx.AsyncClient() as probe:
                    r = await probe.get(f"http://127.0.0.1:{port}/mcp")
                    # Any response means the server is up (likely 401/406).
                    if r.status_code:
                        break
            except Exception:
                import asyncio

                await asyncio.sleep(0.05)

        # Missing token -> 401
        async with httpx.AsyncClient() as client:
            unauthorized = await client.post(
                f"http://127.0.0.1:{port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert unauthorized.status_code == 401

        # Tenant A sees only A; tenant B sees only B.
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        class BearerTransport:
            def __init__(self, url: str, token: str) -> None:
                self._url = url
                self._token = token
                self._client: httpx2.AsyncClient | None = None
                self._cm = None

            async def __aenter__(self):
                self._client = httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=httpx2.Timeout(30.0, read=30.0),
                )
                self._cm = streamable_http_client(self._url, http_client=self._client)
                return await self._cm.__aenter__()

            async def __aexit__(self, *exc):
                assert self._cm is not None
                await self._cm.__aexit__(*exc)
                assert self._client is not None
                await self._client.aclose()

        async with Client(BearerTransport(resource, TOKEN_A)) as client_a:  # type: ignore[arg-type]
            result_a = await client_a.call_tool("list_workbooks", {})
            ids_a = {w["workbook_id"] for w in result_a.structured_content["data"]["workbooks"]}
            assert ids_a == {"tenant-a-timesheet"}

        async with Client(BearerTransport(resource, TOKEN_B)) as client_b:  # type: ignore[arg-type]
            result_b = await client_b.call_tool("list_workbooks", {})
            ids_b = {w["workbook_id"] for w in result_b.structured_content["data"]["workbooks"]}
            assert ids_b == {"tenant-b-timesheet"}
            assert "tenant-a-timesheet" not in ids_b
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_stdio_logging_does_not_write_stdout(config) -> None:
    create_server(config, enable_http_auth=False)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    install_test_principal(principal)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            from timereport_mcp.observability.logging import get_logger

            get_logger("timereport_mcp.test").info("probe message for stdout check")
            # Service call path used by the tool
            from timereport_mcp.excel.workbooks import list_workbooks

            list_workbooks(config, principal)
        assert buf.getvalue() == ""
    finally:
        install_test_principal(None)
