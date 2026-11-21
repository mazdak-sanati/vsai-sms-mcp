# vectorsynapse-timereport-mcp-server

Phase 1 Model Context Protocol (MCP) server for authorized local Excel timesheets and an on-demand SMS gateway.

This repository does **not** implement a runtime AI agent, scheduler, or LLM integration. Those belong to a separate Phase 2 project that **calls** these tools.

## For agents / MCP clients

**Start here:** [`docs/mcp_api.md`](docs/mcp_api.md) — connection, workflows, tool inputs/outputs, and SMS retry behavior.

When connected, the server also exposes the same guidance via MCP `instructions` and each tool’s description.

Compact tool table: [`docs/mcp_interface.md`](docs/mcp_interface.md).

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
python scripts/smoke_mcp_client.py
```

Default SMS provider is `mock`. Live 46elks credentials belong in a local env file (see `tests/local_test.env.example`), never in git.

### Run the MCP server

```bash
# Single-tenant stdio (typical local MCP host)
set TIMEREPORT_CONFIG=config\tenants.example.toml
set TIMEREPORT_STDIO_TENANT=tenant-a
set SMS_PROVIDER=mock
timereport-mcp --transport stdio

# Multi-tenant HTTP
set TIMEREPORT_CONFIG=config\tenants.example.toml
set SMS_PROVIDER=mock
timereport-mcp --transport streamable-http
```

## Documentation

| File | Purpose |
|---|---|
| `docs/mcp_api.md` | **Client/agent usage guide** (primary) |
| `docs/mcp_interface.md` | Compact tool contract |
| `docs/handoff.md` | Phase 1 handoff checklist |
| `docs/plan.md` | Implementation plan |
| `docs/status.md` | Progress log |
| `AGENTS.md` | Coding-agent entry point for developing this repo |
