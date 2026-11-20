# Documentation Index — Phase 1 MCP Server

**This repository builds only a reusable TimeReport MCP server.** A future, separate Phase 2 project will implement the AI agent, provide real Excel timesheets and `.env` secrets (SMS and LLM API keys), and decide when/how often to check and send reminders.

| File | Responsibility |
|---|---|
| `../AGENTS.md` | Entry point for the **coding agent** developing this MCP server |
| `process.md` | Coding/planning/approval cycle and mandatory `status.md` updates |
| `code_structure.md` | Proposed MCP server modules and boundaries |
| `tech_stack.md` | Candidate MCP server technology stack; no LLM framework |
| `improvement.md` | MCP server improvement backlog |
| `mcp_interface.md` | Tool API contract for any future client; **not** an agent specification |
| `plan.md` | Approved implementation plan (M0–M8 complete) |
| `status.md` | Current snapshot and append-only chronology |
| `mcp_api.md` | Consumer-facing MCP API reference with examples |
| `handoff.md` | Phase 1 definition-of-done checklist |

## Phase 1 / Phase 2 separation
**Phase 1:** define and implement callable MCP tools, local Excel validation/read/write, expected-hours checking, tenant isolation, on-demand SMS interface with mock provider and security tests.

**Phase 2 (outside this repository):** build the runtime agent, supply Excel files, use `.env` for SMS and LLM API keys, select models, decide check frequency, invoke the MCP tools and orchestrate reminders.

No `runtime/TIMEREPORT_AGENT.md`, scheduler, LLM integration or Phase 2 agent code belongs in this project.
