# Documentation Index — Phase 1 MCP Server

**This repository builds only a reusable TimeReport MCP server.** A separate Phase 2 project implements the AI agent, supplies real Excel files and secrets, and decides when to call tools.

## If you are connecting an agent or MCP client

1. Read **[`mcp_api.md`](mcp_api.md)** (connection, workflows, schemas, SMS replay rules).
2. Skim **[`mcp_interface.md`](mcp_interface.md)** for the compact tool table.
3. Configure transport (stdio or Streamable HTTP) as described in `mcp_api.md`.

The running server also publishes MCP `instructions` and per-tool descriptions that match that guide.

## All docs

| File | Responsibility |
|---|---|
| [`mcp_api.md`](mcp_api.md) | **Primary client/agent usage guide** |
| [`mcp_interface.md`](mcp_interface.md) | Compact tool API contract |
| [`handoff.md`](handoff.md) | Phase 1 definition-of-done / connect checklist |
| [`../AGENTS.md`](../AGENTS.md) | Entry point for the **coding agent** developing this server |
| [`process.md`](process.md) | Planning/approval cycle for contributors |
| [`code_structure.md`](code_structure.md) | Module layout |
| [`tech_stack.md`](tech_stack.md) | Stack decisions |
| [`improvement.md`](improvement.md) | Backlog |
| [`plan.md`](plan.md) | Approved implementation plan |
| [`status.md`](status.md) | Progress chronology |

## Phase 1 / Phase 2 separation

**Phase 1 (this repo):** MCP tools, tenant isolation, Excel read/write/validation, expected-hours reporting, on-demand SMS with mock (and optional 46elks).

**Phase 2 (separate):** runtime agent, prompts, schedule, real workbooks, SMS + LLM secrets in that project’s `.env`, decisions about when to remind.
