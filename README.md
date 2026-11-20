# vectorsynapse-timereport-mcp-server

Phase 1 Model Context Protocol (MCP) server for authorized local Excel timesheets and an on-demand SMS gateway.

This repository does **not** implement a runtime AI agent, scheduler, or LLM integration. Those belong to a separate Phase 2 project.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
python scripts/smoke_mcp_client.py
```

Default SMS provider is `mock`. Do not set live 46elks credentials in this repository.

## Documentation

| File | Purpose |
|---|---|
| `AGENTS.md` | Coding-agent entry point |
| `docs/plan.md` | Approved implementation plan |
| `docs/status.md` | Progress log |
| `docs/mcp_api.md` | Consumer MCP API reference |
| `docs/mcp_interface.md` | Tool contract |
| `docs/handoff.md` | Phase 1 handoff checklist |
